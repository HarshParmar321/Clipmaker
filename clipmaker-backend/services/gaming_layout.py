"""Detect an OBS facecam and describe the Gameplay + Reaction composition."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from statistics import median

from services.face_tracker import FaceBox, _CascadeDetector, _create_detector
from services.render_settings import FacecamCorner

logger = logging.getLogger("clipmaker.gaming_layout")


@dataclass(frozen=True)
class FacecamRegion:
    """A normalized crop inside the original source frame."""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class FacecamAnalysis:
    region: FacecamRegion
    confidence: float
    detection_ratio: float
    detector: str
    corner: FacecamCorner
    used_fallback: bool = False


@dataclass(frozen=True)
class _Detection:
    sample: int
    x: float
    y: float
    width: float
    height: float
    confidence: float
    corner: FacecamCorner

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(float(value), maximum))


def region_for_corner(corner: FacecamCorner) -> FacecamRegion:
    """Return a safe manual/fallback OBS camera crop for a screen corner."""
    corner = FacecamCorner(corner)
    if corner == FacecamCorner.AUTO:
        corner = FacecamCorner.BOTTOM_RIGHT
    width, height, inset = 0.32, 0.34, 0.015
    x = inset if corner in {FacecamCorner.TOP_LEFT, FacecamCorner.BOTTOM_LEFT} else 1 - width - inset
    y = inset if corner in {FacecamCorner.TOP_LEFT, FacecamCorner.TOP_RIGHT} else 1 - height - inset
    return FacecamRegion(x, y, width, height)


def _nearest_corner(center_x: float, center_y: float) -> FacecamCorner:
    horizontal = "left" if center_x < 0.5 else "right"
    vertical = "top" if center_y < 0.5 else "bottom"
    return FacecamCorner(f"{vertical}_{horizontal}")


def _corner_distance(center_x: float, center_y: float, corner: FacecamCorner) -> float:
    target_x = 0.0 if corner in {FacecamCorner.TOP_LEFT, FacecamCorner.BOTTOM_LEFT} else 1.0
    target_y = 0.0 if corner in {FacecamCorner.TOP_LEFT, FacecamCorner.TOP_RIGHT} else 1.0
    return math.hypot(center_x - target_x, center_y - target_y)


def _candidate(face: FaceBox, frame_width: int, frame_height: int, sample: int) -> _Detection | None:
    x = face.x / max(frame_width, 1)
    y = face.y / max(frame_height, 1)
    width = face.width / max(frame_width, 1)
    height = face.height / max(frame_height, 1)
    area = width * height
    center_x, center_y = x + width / 2, y + height / 2
    corner = _nearest_corner(center_x, center_y)
    # OBS facecams are normally small and stay close to a screen corner. Large,
    # central faces are likely part of the gameplay/cutscene and are ignored.
    if (
        area < 0.00045
        or area > 0.075
        or 0.36 < center_x < 0.64
        or 0.36 < center_y < 0.64
        or _corner_distance(center_x, center_y, corner) > 0.72
    ):
        return None
    return _Detection(sample, x, y, width, height, face.confidence, corner)


def choose_facecam_region(
    detections: list[_Detection], sample_count: int, preferred_corner: FacecamCorner = FacecamCorner.AUTO
) -> FacecamAnalysis:
    """Choose the small, stable, repeatedly detected corner face."""
    preferred_corner = FacecamCorner(preferred_corner)
    candidates = [
        item for item in detections
        if preferred_corner == FacecamCorner.AUTO or item.corner == preferred_corner
    ]
    if not candidates:
        fallback_corner = preferred_corner if preferred_corner != FacecamCorner.AUTO else FacecamCorner.BOTTOM_RIGHT
        return FacecamAnalysis(region_for_corner(fallback_corner), 0.0, 0.0, "corner-fallback", fallback_corner, True)

    clusters: list[list[_Detection]] = []
    for item in candidates:
        target = None
        for cluster in clusters:
            anchor = cluster[-1]
            if (
                anchor.corner == item.corner
                and abs(anchor.center_x - item.center_x) <= 0.105
                and abs(anchor.center_y - item.center_y) <= 0.105
            ):
                target = cluster
                break
        if target is None:
            clusters.append([item])
        else:
            target.append(item)

    # Repeated presence matters much more than one strong face.
    def score(cluster: list[_Detection]) -> float:
        unique_samples = len({item.sample for item in cluster})
        repeat = unique_samples / max(sample_count, 1)
        confidence = sum(item.confidence for item in cluster) / len(cluster)
        centers_x = [item.center_x for item in cluster]
        centers_y = [item.center_y for item in cluster]
        stability = 1 - min(
            (max(centers_x) - min(centers_x)) + (max(centers_y) - min(centers_y)),
            0.3,
        ) / 0.3
        return repeat * 0.72 + confidence * 0.13 + stability * 0.15

    selected = max(clusters, key=score)
    detection_ratio = len({item.sample for item in selected}) / max(sample_count, 1)
    typical = _Detection(
        0,
        median(item.x for item in selected),
        median(item.y for item in selected),
        median(item.width for item in selected),
        median(item.height for item in selected),
        median(item.confidence for item in selected),
        selected[0].corner,
    )
    # Expand beyond the face to include the streamer's shoulders and background.
    crop_width = _clamp(typical.width * 4.3, 0.20, 0.40)
    crop_height = _clamp(typical.height * 3.8, 0.22, 0.46)
    crop_x = _clamp(typical.center_x - crop_width / 2, 0.0, 1 - crop_width)
    crop_y = _clamp(typical.center_y - crop_height * 0.40, 0.0, 1 - crop_height)
    confidence = _clamp(score(selected))
    if detection_ratio < 0.22:
        return FacecamAnalysis(region_for_corner(typical.corner), confidence, detection_ratio, "corner-fallback", typical.corner, True)
    return FacecamAnalysis(
        FacecamRegion(crop_x, crop_y, crop_width, crop_height),
        confidence,
        detection_ratio,
        "face-detection",
        typical.corner,
        False,
    )


def detect_facecam_region(
    source_path: Path,
    start: float,
    end: float,
    preferred_corner: FacecamCorner = FacecamCorner.AUTO,
    *,
    samples: int = 16,
) -> FacecamAnalysis:
    """Detect a stable OBS facecam using evenly spaced frames and cache it."""
    try:
        stat = source_path.stat()
    except OSError:
        corner = preferred_corner if preferred_corner != FacecamCorner.AUTO else FacecamCorner.BOTTOM_RIGHT
        return FacecamAnalysis(region_for_corner(corner), 0, 0, "corner-fallback", corner, True)
    return _detect_facecam_cached(
        str(source_path.resolve()), stat.st_mtime_ns, stat.st_size,
        round(float(start), 3), round(float(end), 3), FacecamCorner(preferred_corner), max(6, min(samples, 30)),
    )


@lru_cache(maxsize=96)
def _detect_facecam_cached(
    source_path_text: str,
    _modified_at: int,
    _size_bytes: int,
    start: float,
    end: float,
    preferred_corner: FacecamCorner,
    samples: int,
) -> FacecamAnalysis:
    try:
        import cv2
    except ImportError:
        corner = preferred_corner if preferred_corner != FacecamCorner.AUTO else FacecamCorner.BOTTOM_RIGHT
        return FacecamAnalysis(region_for_corner(corner), 0, 0, "corner-fallback", corner, True)

    capture = cv2.VideoCapture(source_path_text)
    if not capture.isOpened():
        corner = preferred_corner if preferred_corner != FacecamCorner.AUTO else FacecamCorner.BOTTOM_RIGHT
        return FacecamAnalysis(region_for_corner(corner), 0, 0, "corner-fallback", corner, True)
    detector = _create_detector(cv2)
    found: list[_Detection] = []
    duration = max(end - start, 0.1)
    try:
        for sample in range(samples):
            position = start + duration * ((sample + 0.5) / samples)
            capture.set(cv2.CAP_PROP_POS_MSEC, position * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            scale = min(1.0, 720 / max(frame.shape[1], frame.shape[0], 1))
            if scale < 1:
                frame = cv2.resize(frame, None, fx=scale, fy=scale)
            try:
                faces = detector.detect(frame)
            except Exception:
                detector = _CascadeDetector(cv2)
                faces = detector.detect(frame)
            for face in faces:
                item = _candidate(face, frame.shape[1], frame.shape[0], sample)
                if item is not None:
                    found.append(item)
    finally:
        capture.release()
    analysis = choose_facecam_region(found, samples, preferred_corner)
    return FacecamAnalysis(
        analysis.region,
        analysis.confidence,
        analysis.detection_ratio,
        getattr(detector, "name", analysis.detector) if not analysis.used_fallback else analysis.detector,
        analysis.corner,
        analysis.used_fallback,
    )
