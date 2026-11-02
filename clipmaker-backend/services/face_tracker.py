"""Local, privacy-preserving subject tracking for vertical reframing.

The primary detector is OpenCV YuNet. A frontal/profile Haar detector remains
available as a zero-configuration fallback so rendering never depends on a
remote computer-vision API.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from statistics import median

logger = logging.getLogger("clipmaker.face_tracker")


def source_zoom_ceiling(source_height: int) -> float:
    """How far we may push in before the crop is mostly invented pixels.

    A 9:16 column out of a landscape frame is already an upscale; zoom
    multiplies it. Capping here rather than at render time means the studio
    shows the zoom that will actually be used.
    """
    from config import MAX_UPSCALE_FACTOR
    from services.render_settings import OutputFormat, TARGET_DIMENSIONS

    target_height = TARGET_DIMENSIONS[OutputFormat.VERTICAL][1]
    ceiling = MAX_UPSCALE_FACTOR * max(int(source_height), 1) / target_height
    return max(1.0, round(min(3.0, ceiling), 2))

YUNET_MODEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "models"
    / "face_detection_yunet_2023mar.onnx"
)


@dataclass(frozen=True)
class FocusPoint:
    time: float
    focus: float
    confidence: float
    vertical_focus: float = 0.5
    scene_cut: bool = False


@dataclass(frozen=True)
class FaceBox:
    x: float
    y: float
    width: float
    height: float
    confidence: float = 1.0

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2

    @property
    def area(self) -> float:
        return max(self.width, 0) * max(self.height, 0)


@dataclass(frozen=True)
class ReframeAnalysis:
    points: list[FocusPoint]
    zoom: float = 1.0
    detection_ratio: float = 0.0
    detector: str = "center"
    scene_cuts: int = 0
    subject_switches: int = 0


@dataclass
class _SubjectTrack:
    id: int
    box: FaceBox
    last_seen: float
    hits: int = 1
    mouth_motion: float = 0.0
    mouth_patch: object | None = None


@dataclass
class _TrackingSample:
    time: float
    box: FaceBox | None
    confidence: float
    scene_cut: bool
    face_count: int


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return float(max(minimum, min(float(value), maximum)))


def face_center_to_crop_focus(
    face_center_x: float,
    source_width: int,
    source_height: int,
    target_width: int = 720,
    target_height: int = 1280,
) -> float:
    """Convert a normalized face centre to FFmpeg's available crop travel."""
    source_ratio = source_width / max(source_height, 1)
    target_ratio = target_width / max(target_height, 1)
    if source_ratio <= target_ratio:
        return 0.5
    visible_fraction = target_ratio / source_ratio
    available = 1 - visible_fraction
    left = max(0.0, min(face_center_x - visible_fraction / 2, available))
    return _clamp(left / available)


def _coordinate_to_crop_focus(
    coordinate: float,
    visible_fraction: float,
    desired_position: float,
) -> float:
    available = max(1 - visible_fraction, 0)
    if available <= 0.0001:
        return 0.5
    offset = _clamp(
        coordinate - visible_fraction * desired_position,
        0.0,
        available,
    )
    return _clamp(offset / available)


def face_center_to_crop_position(
    face_center_x: float,
    face_center_y: float,
    source_width: int,
    source_height: int,
    *,
    zoom: float = 1.0,
    target_width: int = 720,
    target_height: int = 1280,
) -> tuple[float, float]:
    """Map a face centre to a crop with natural headroom."""
    source_ratio = source_width / max(source_height, 1)
    target_ratio = target_width / max(target_height, 1)
    zoom = _clamp(zoom, 1.0, 3.0)
    if source_ratio > target_ratio:
        base_visible_width, base_visible_height = target_ratio / source_ratio, 1.0
    else:
        base_visible_width, base_visible_height = 1.0, source_ratio / target_ratio
    visible_width = _clamp(base_visible_width / zoom, 0.001, 1.0)
    visible_height = _clamp(base_visible_height / zoom, 0.001, 1.0)
    return (
        _coordinate_to_crop_focus(face_center_x, visible_width, 0.5),
        _coordinate_to_crop_focus(face_center_y, visible_height, 0.38),
    )


def _smooth_value(
    current: float,
    target: float,
    delta_time: float,
    smoothing_seconds: float,
    max_speed_per_second: float,
) -> float:
    alpha = 1 - math.exp(-delta_time / max(smoothing_seconds, 0.1))
    candidate = current + alpha * (target - current)
    maximum_move = max_speed_per_second * delta_time
    movement = max(-maximum_move, min(candidate - current, maximum_move))
    return _clamp(current + movement)


def smooth_focus_points(
    detections: list[FocusPoint],
    *,
    default_focus: float = 0.5,
    default_vertical_focus: float = 0.5,
    smoothing_seconds: float = 0.9,
    max_speed_per_second: float = 0.24,
    max_vertical_speed_per_second: float = 0.18,
    detection_hold_seconds: float = 1.1,
) -> list[FocusPoint]:
    """Hold short gaps, reset on scene cuts, and limit camera movement."""
    if not detections:
        return []
    first = detections[0]
    current_x = first.focus if first.confidence > 0 else default_focus
    current_y = first.vertical_focus if first.confidence > 0 else default_vertical_focus
    last_time = first.time
    last_detection_time = first.time if first.confidence > 0 else -math.inf
    smoothed: list[FocusPoint] = []
    for point in detections:
        delta_time = max(point.time - last_time, 0.04)
        if point.confidence > 0:
            target_x = point.focus
            target_y = point.vertical_focus
            last_detection_time = point.time
        elif point.time - last_detection_time <= detection_hold_seconds:
            target_x, target_y = current_x, current_y
        else:
            target_x, target_y = default_focus, default_vertical_focus
        if point.scene_cut and point.confidence > 0:
            current_x, current_y = target_x, target_y
        else:
            current_x = _smooth_value(
                current_x,
                target_x,
                delta_time,
                smoothing_seconds,
                max_speed_per_second,
            )
            current_y = _smooth_value(
                current_y,
                target_y,
                delta_time,
                smoothing_seconds,
                max_vertical_speed_per_second,
            )
        smoothed.append(
            FocusPoint(
                round(point.time, 3),
                current_x,
                point.confidence,
                current_y,
                point.scene_cut,
            )
        )
        last_time = point.time
    return smoothed


def simplify_focus_points(
    points: list[FocusPoint], tolerance: float = 0.012, max_points: int = 80
) -> list[FocusPoint]:
    if len(points) <= 2:
        return points
    kept = [points[0]]
    for point in points[1:-1]:
        previous = kept[-1]
        if (
            point.scene_cut
            or abs(point.focus - previous.focus) >= tolerance
            or abs(point.vertical_focus - previous.vertical_focus) >= tolerance
        ):
            kept.append(point)
    kept.append(points[-1])
    if len(kept) <= max_points:
        return kept
    protected = {0, len(kept) - 1}
    protected.update(index for index, point in enumerate(kept) if point.scene_cut)
    if len(protected) >= max_points:
        ordered = sorted(protected)
        stride = (len(ordered) - 1) / max(max_points - 1, 1)
        selected = {
            ordered[min(round(index * stride), len(ordered) - 1)]
            for index in range(max_points)
        }
        return [point for index, point in enumerate(kept) if index in selected]
    remaining_slots = max(max_points - len(protected), 0)
    candidates = [index for index in range(len(kept)) if index not in protected]
    if remaining_slots and candidates:
        stride = max(len(candidates) / remaining_slots, 1)
        protected.update(
            candidates[min(round(index * stride), len(candidates) - 1)]
            for index in range(remaining_slots)
        )
    return [point for index, point in enumerate(kept) if index in protected]


def _focus_at_time(points: list[FocusPoint], time_seconds: float) -> FocusPoint:
    ordered = sorted(points, key=lambda point: point.time)
    if time_seconds <= ordered[0].time:
        point = ordered[0]
        return FocusPoint(time_seconds, point.focus, point.confidence, point.vertical_focus)
    if time_seconds >= ordered[-1].time:
        point = ordered[-1]
        return FocusPoint(time_seconds, point.focus, point.confidence, point.vertical_focus)
    after_index = next(
        index for index, point in enumerate(ordered) if point.time >= time_seconds
    )
    before, after = ordered[after_index - 1], ordered[after_index]
    progress = (time_seconds - before.time) / max(after.time - before.time, 0.001)
    return FocusPoint(
        time_seconds,
        before.focus + (after.focus - before.focus) * progress,
        before.confidence + (after.confidence - before.confidence) * progress,
        before.vertical_focus
        + (after.vertical_focus - before.vertical_focus) * progress,
    )


def slice_focus_points(
    points: list[FocusPoint], start: float, end: float
) -> list[FocusPoint]:
    """Trim a cached camera path and rebase it to the rendered clip clock."""
    if not points or end <= start:
        return []
    selected = [_focus_at_time(points, start)]
    selected.extend(point for point in points if start < point.time < end)
    selected.append(_focus_at_time(points, end))
    rebased: list[FocusPoint] = []
    for point in selected:
        time = round(point.time - start, 3)
        if rebased and time <= rebased[-1].time:
            continue
        rebased.append(
            FocusPoint(
                time,
                point.focus,
                point.confidence,
                point.vertical_focus,
                point.scene_cut,
            )
        )
    return rebased


def _box_iou(first: FaceBox, second: FaceBox) -> float:
    left = max(first.x, second.x)
    top = max(first.y, second.y)
    right = min(first.x + first.width, second.x + second.width)
    bottom = min(first.y + first.height, second.y + second.height)
    intersection = max(right - left, 0) * max(bottom - top, 0)
    union = first.area + second.area - intersection
    return intersection / union if union > 0 else 0.0


def _normalised_center_distance(
    first: FaceBox, second: FaceBox, diagonal: float
) -> float:
    return math.hypot(
        first.center_x - second.center_x,
        first.center_y - second.center_y,
    ) / max(diagonal, 1)


def _nms(boxes: list[FaceBox], threshold: float = 0.32) -> list[FaceBox]:
    remaining = sorted(boxes, key=lambda item: item.confidence, reverse=True)
    kept: list[FaceBox] = []
    while remaining:
        winner = remaining.pop(0)
        kept.append(winner)
        remaining = [box for box in remaining if _box_iou(winner, box) < threshold]
    return kept


def _mouth_patch(cv2, gray, box: FaceBox):
    frame_height, frame_width = gray.shape[:2]
    x1 = max(round(box.x + box.width * 0.14), 0)
    x2 = min(round(box.x + box.width * 0.86), frame_width)
    y1 = max(round(box.y + box.height * 0.52), 0)
    y2 = min(round(box.y + box.height * 0.94), frame_height)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    patch = cv2.resize(gray[y1:y2, x1:x2], (48, 24))
    return cv2.GaussianBlur(patch, (3, 3), 0)


class _SubjectTracker:
    """Associate faces across frames and keep one stable active subject."""

    def __init__(self, cv2, frame_width: int, frame_height: int):
        self.cv2 = cv2
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.diagonal = math.hypot(frame_width, frame_height)
        self.tracks: dict[int, _SubjectTrack] = {}
        self.next_id = 1
        self.active_id: int | None = None
        self.challenger_id: int | None = None
        self.challenger_streak = 0
        self.switches = 0

    def reset(self) -> None:
        self.tracks.clear()
        self.active_id = None
        self.challenger_id = None
        self.challenger_streak = 0

    def _motion(self, previous, current) -> float:
        if previous is None or current is None:
            return 0.0
        difference = float(self.cv2.absdiff(previous, current).mean()) / 255
        return _clamp(difference / 0.12)

    def _score(self, track: _SubjectTrack) -> float:
        frame_area = max(self.frame_width * self.frame_height, 1)
        prominence = _clamp(math.sqrt((track.box.area / frame_area) / 0.075))
        distance_from_center = math.hypot(
            track.box.center_x / self.frame_width - 0.5,
            track.box.center_y / self.frame_height - 0.46,
        )
        centrality = _clamp(1 - distance_from_center / 0.72)
        stability = _clamp(track.hits / 4)
        score = (
            0.36 * prominence
            + 0.20 * _clamp(track.box.confidence)
            + 0.27 * _clamp(track.mouth_motion)
            + 0.12 * stability
            + 0.05 * centrality
        )
        if track.id == self.active_id:
            score += 0.16
        return score

    def update(
        self,
        faces: list[FaceBox],
        gray,
        time_seconds: float,
        *,
        scene_cut: bool = False,
    ) -> tuple[FaceBox | None, float]:
        if scene_cut:
            self.reset()
        self.tracks = {
            track_id: track
            for track_id, track in self.tracks.items()
            if time_seconds - track.last_seen <= 1.25
        }
        unmatched_tracks = set(self.tracks)
        assignments: list[tuple[FaceBox, _SubjectTrack | None]] = []
        for face in sorted(faces, key=lambda item: item.area, reverse=True):
            best_track = None
            best_match = -1.0
            for track_id in unmatched_tracks:
                track = self.tracks[track_id]
                distance = _normalised_center_distance(face, track.box, self.diagonal)
                overlap = _box_iou(face, track.box)
                if overlap < 0.04 and distance > 0.20:
                    continue
                match = overlap * 0.68 + (1 - distance) * 0.32
                if match > best_match:
                    best_match, best_track = match, track
            if best_track:
                unmatched_tracks.remove(best_track.id)
            assignments.append((face, best_track))

        visible_track_ids: set[int] = set()
        for face, track in assignments:
            patch = _mouth_patch(self.cv2, gray, face)
            if track is None:
                track = _SubjectTrack(
                    id=self.next_id,
                    box=face,
                    last_seen=time_seconds,
                    mouth_patch=patch,
                )
                self.tracks[track.id] = track
                self.next_id += 1
            else:
                motion = self._motion(track.mouth_patch, patch)
                track.mouth_motion = 0.58 * track.mouth_motion + 0.42 * motion
                track.box = face
                track.last_seen = time_seconds
                track.hits += 1
                track.mouth_patch = patch
            visible_track_ids.add(track.id)

        visible = [self.tracks[track_id] for track_id in visible_track_ids]
        if not visible:
            return None, 0.0
        ranked = sorted(visible, key=self._score, reverse=True)
        best = ranked[0]
        active = self.tracks.get(self.active_id) if self.active_id else None
        if active is None or active.id not in visible_track_ids:
            previous_id = self.active_id
            self.active_id = best.id
            if previous_id is not None and previous_id != best.id:
                self.switches += 1
            self.challenger_id = None
            self.challenger_streak = 0
        elif best.id != active.id and self._score(best) > self._score(active) + 0.12:
            if self.challenger_id == best.id:
                self.challenger_streak += 1
            else:
                self.challenger_id = best.id
                self.challenger_streak = 1
            if self.challenger_streak >= 2:
                self.active_id = best.id
                self.switches += 1
                self.challenger_id = None
                self.challenger_streak = 0
        else:
            self.challenger_id = None
            self.challenger_streak = 0

        selected = self.tracks.get(self.active_id)
        if selected is None or selected.id not in visible_track_ids:
            return None, 0.0
        confidence = _clamp(
            0.55 * selected.box.confidence
            + 0.25 * _clamp(selected.hits / 4)
            + 0.20 * selected.mouth_motion
        )
        return selected.box, confidence


class _YuNetDetector:
    name = "yunet"

    def __init__(self, cv2):
        self.detector = cv2.FaceDetectorYN.create(
            str(YUNET_MODEL_PATH),
            "",
            (320, 320),
            0.62,
            0.3,
            500,
        )

    def detect(self, frame) -> list[FaceBox]:
        height, width = frame.shape[:2]
        self.detector.setInputSize((width, height))
        _status, faces = self.detector.detect(frame)
        if faces is None:
            return []
        return _nms(
            [
                FaceBox(
                    float(row[0]),
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    _clamp(float(row[14])),
                )
                for row in faces
                if row[2] > 4 and row[3] > 4
            ]
        )


class _CascadeDetector:
    name = "haar-fallback"

    def __init__(self, cv2):
        self.cv2 = cv2
        root = Path(cv2.data.haarcascades)
        self.frontal = cv2.CascadeClassifier(
            str(root / "haarcascade_frontalface_default.xml")
        )
        self.profile = cv2.CascadeClassifier(str(root / "haarcascade_profileface.xml"))

    def detect(self, frame) -> list[FaceBox]:
        gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
        gray = self.cv2.equalizeHist(gray)
        minimum = max(28, round(min(gray.shape[:2]) * 0.055))
        boxes: list[FaceBox] = []
        frontal = self.frontal.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(minimum, minimum),
        )
        boxes.extend(FaceBox(*map(float, item), 0.68) for item in frontal)
        for mirrored in (False, True):
            image = self.cv2.flip(gray, 1) if mirrored else gray
            profiles = self.profile.detectMultiScale(
                image,
                scaleFactor=1.12,
                minNeighbors=5,
                minSize=(minimum, minimum),
            )
            for x, y, width, height in profiles:
                source_x = gray.shape[1] - x - width if mirrored else x
                boxes.append(
                    FaceBox(
                        float(source_x),
                        float(y),
                        float(width),
                        float(height),
                        0.54,
                    )
                )
        return _nms(boxes)


def _create_detector(cv2):
    if YUNET_MODEL_PATH.is_file() and hasattr(cv2, "FaceDetectorYN"):
        try:
            return _YuNetDetector(cv2)
        except Exception as exc:
            logger.warning("YuNet could not start; using Haar fallback: %s", exc)
    return _CascadeDetector(cv2)


def _scene_signature(cv2, frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
    return cv2.normalize(histogram, histogram).flatten()


def _suggest_zoom(
    face_height_ratios: list[float],
    *,
    detection_ratio: float,
    average_face_count: float,
) -> float:
    if not face_height_ratios or detection_ratio < 0.2:
        return 1.0
    typical_face_height = median(face_height_ratios)
    zoom = 0.29 / max(typical_face_height, 0.08)
    maximum = 1.18 if average_face_count >= 1.7 else 1.35
    if detection_ratio < 0.48:
        maximum = min(maximum, 1.1)
    zoom = _clamp(zoom, 1.0, maximum)
    return round(round(zoom / 0.05) * 0.05, 2)


def track_subject(
    source_path: Path,
    start: float,
    end: float,
    *,
    sample_interval: float = 0.35,
) -> ReframeAnalysis:
    """Analyse a clip and return a cached, composition-aware camera path."""
    try:
        stat = source_path.stat()
    except OSError:
        return ReframeAnalysis([])
    return _track_subject_cached(
        str(source_path.resolve()),
        stat.st_mtime_ns,
        stat.st_size,
        round(float(start), 3),
        round(float(end), 3),
        round(float(sample_interval), 3),
    )


@lru_cache(maxsize=96)
def _track_subject_cached(
    source_path_text: str,
    _modified_at: int,
    _size_bytes: int,
    start: float,
    end: float,
    sample_interval: float,
) -> ReframeAnalysis:
    try:
        import cv2
    except ImportError:
        logger.warning("OpenCV is unavailable; falling back to centred reframing")
        return ReframeAnalysis([])

    source_path = Path(source_path_text)
    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        logger.warning("Could not open %s for subject tracking", source_path)
        return ReframeAnalysis([])
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frames_per_second = max(float(capture.get(cv2.CAP_PROP_FPS)), 1.0)
    detector = _create_detector(cv2)
    duration = max(end - start, 0.0)
    samples: list[_TrackingSample] = []
    previous_signature = None
    scene_cuts = 0
    tracker: _SubjectTracker | None = None
    next_sample_time = 0.0
    frame_index = 0
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, max(start, 0) * 1000)
        while next_sample_time <= duration + 0.001:
            ok, frame = capture.read()
            if not ok:
                break
            relative_time = frame_index / frames_per_second
            frame_index += 1
            if relative_time + 0.0001 < next_sample_time:
                continue
            next_sample_time += max(sample_interval, 0.15)
            scale = min(1.0, 720 / max(frame.shape[1], frame.shape[0], 1))
            if scale < 1:
                frame = cv2.resize(frame, None, fx=scale, fy=scale)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            signature = _scene_signature(cv2, frame)
            scene_cut = False
            if previous_signature is not None:
                distance = cv2.compareHist(
                    previous_signature.astype("float32"),
                    signature.astype("float32"),
                    cv2.HISTCMP_BHATTACHARYYA,
                )
                scene_cut = bool(distance >= 0.58)
                scene_cuts += int(scene_cut)
            previous_signature = signature
            if tracker is None:
                tracker = _SubjectTracker(cv2, frame.shape[1], frame.shape[0])
            try:
                faces = detector.detect(frame)
            except Exception as exc:
                if getattr(detector, "name", "") == "yunet":
                    logger.warning("YuNet inference failed; switching to Haar: %s", exc)
                    detector = _CascadeDetector(cv2)
                    faces = detector.detect(frame)
                else:
                    logger.warning("Face detection failed for one frame: %s", exc)
                    faces = []
            selected, confidence = tracker.update(
                faces,
                gray,
                relative_time,
                scene_cut=scene_cut,
            )
            samples.append(
                _TrackingSample(
                    min(relative_time, duration),
                    selected,
                    confidence,
                    scene_cut,
                    len(faces),
                )
            )
    finally:
        capture.release()

    detected = [sample for sample in samples if sample.box is not None]
    detection_ratio = len(detected) / max(len(samples), 1)
    if detection_ratio < 0.14:
        logger.info(
            "Subject detection confidence too low for %s (%.0f%%); using centre crop",
            source_path.name,
            detection_ratio * 100,
        )
        return ReframeAnalysis(
            [],
            detection_ratio=detection_ratio,
            detector=detector.name,
            scene_cuts=scene_cuts,
            subject_switches=tracker.switches if tracker else 0,
        )

    scale = min(1.0, 720 / max(source_width, source_height, 1))
    scaled_width = source_width * scale
    scaled_height = source_height * scale
    face_heights = [sample.box.height / max(scaled_height, 1) for sample in detected]
    average_face_count = sum(sample.face_count for sample in samples) / max(
        len(samples), 1
    )
    zoom = _suggest_zoom(
        face_heights,
        detection_ratio=detection_ratio,
        average_face_count=average_face_count,
    )
    ceiling = source_zoom_ceiling(source_height)
    if zoom > ceiling:
        logger.info(
            "Smart reframe zoom limited to %.2fx (wanted %.2fx): a %dp source "
            "cannot fill a vertical frame at more than that without upscaling",
            ceiling,
            zoom,
            source_height,
        )
        zoom = ceiling
    raw_points: list[FocusPoint] = []
    for sample in samples:
        if sample.box is None:
            raw_points.append(
                FocusPoint(sample.time, 0.5, 0.0, 0.5, sample.scene_cut)
            )
            continue
        crop_x, crop_y = face_center_to_crop_position(
            sample.box.center_x / max(scaled_width, 1),
            sample.box.center_y / max(scaled_height, 1),
            source_width,
            source_height,
            zoom=zoom,
        )
        raw_points.append(
            FocusPoint(
                sample.time,
                crop_x,
                sample.confidence,
                crop_y,
                sample.scene_cut,
            )
        )
    points = simplify_focus_points(smooth_focus_points(raw_points))
    analysis = ReframeAnalysis(
        points,
        zoom,
        detection_ratio,
        detector.name,
        scene_cuts,
        tracker.switches if tracker else 0,
    )
    logger.info(
        "Smart reframe %s: %s, %.0f%% detection, %d points, %.2fx zoom, "
        "%d scene cuts, %d subject switches",
        source_path.name,
        detector.name,
        detection_ratio * 100,
        len(points),
        zoom,
        scene_cuts,
        analysis.subject_switches,
    )
    return analysis


def track_faces(
    source_path: Path,
    start: float,
    end: float,
    *,
    sample_interval: float = 0.35,
) -> list[FocusPoint]:
    """Backward-compatible wrapper returning only the camera path."""
    return track_subject(
        source_path,
        start,
        end,
        sample_interval=sample_interval,
    ).points
