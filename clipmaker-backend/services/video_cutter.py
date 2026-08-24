"""FFmpeg cutting, responsive reframing, probing, and thumbnails."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from services.exceptions import VideoDownloadError
from services.face_tracker import (
    FocusPoint,
    ReframeAnalysis,
    slice_focus_points,
    track_subject,
)
from services.render_settings import OutputFormat, ReframeMode, TARGET_DIMENSIONS


@dataclass(frozen=True)
class CropKeyframe:
    time: float
    x: float
    y: float


def _run(args: list[str], step_description: str) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise VideoDownloadError(
            user_message=f"Something went wrong while {step_description}.",
            technical_detail=f"command exit {result.returncode}: {result.stderr[-2000:]}",
        )
    return result


def probe_media(source_path: Path) -> dict:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(source_path),
        ],
        "checking the video",
    )
    try:
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        return {
            "duration": float(payload["format"]["duration"]),
            "width": int(stream["width"]),
            "height": int(stream["height"]),
        }
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VideoDownloadError(
            user_message="We could not read this video's dimensions or duration.",
            technical_detail=result.stdout[-1000:],
        ) from exc


def probe_duration(source_path: Path) -> float:
    return probe_media(source_path)["duration"]


def render_dimensions(source_path: Path, output_format: OutputFormat) -> tuple[int, int]:
    output_format = OutputFormat(output_format)
    if output_format in TARGET_DIMENSIONS:
        return TARGET_DIMENSIONS[output_format]
    media = probe_media(source_path)
    return (media["width"] // 2 * 2, media["height"] // 2 * 2)


def _video_filter(
    output_format: OutputFormat,
    crop_x: float,
    crop_y: float = 0.5,
    crop_zoom: float = 1.0,
    ass_path: Path | None = None,
    focus_points: list[FocusPoint] | None = None,
    crop_keyframes: list[CropKeyframe] | None = None,
) -> str | None:
    output_format = OutputFormat(output_format)
    filters: list[str] = []
    if output_format in TARGET_DIMENSIONS:
        width, height = TARGET_DIMENSIONS[output_format]
        zoom = max(1.0, min(float(crop_zoom), 3.0))
        scaled_width = max(width, round(width * zoom / 2) * 2)
        scaled_height = max(height, round(height * zoom / 2) * 2)
        horizontal = max(0.0, min(float(crop_x), 1.0))
        vertical = max(0.0, min(float(crop_y), 1.0))
        if focus_points:
            x_expression = build_crop_expression(focus_points, horizontal)
            y_expression = build_vertical_crop_expression(focus_points, vertical)
        else:
            x_expression = build_keyframe_expression(
                crop_keyframes or [], "x", horizontal, "iw", "ow"
            )
            y_expression = build_keyframe_expression(
                crop_keyframes or [], "y", vertical, "ih", "oh"
            )
        filters.append(
            "setpts=PTS-STARTPTS,"
            f"scale={scaled_width}:{scaled_height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:x='{x_expression}':y='{y_expression}',setsar=1"
        )
    if ass_path:
        escaped_ass = str(ass_path).replace("\\", "\\\\").replace(":", "\\:")
        filters.append(f"ass={escaped_ass}")
    return ",".join(filters) if filters else None


def build_crop_expression(
    focus_points: list[FocusPoint], fallback_focus: float = 0.5
) -> str:
    """Build a piecewise-linear, frame-evaluated FFmpeg crop expression."""
    values = [(point.time, point.focus) for point in focus_points]
    return _build_axis_expression(values, fallback_focus, "iw", "ow")


def build_vertical_crop_expression(
    focus_points: list[FocusPoint], fallback_focus: float = 0.5
) -> str:
    """Build the vertical Smart Reframe path, including natural headroom."""
    values = [(point.time, point.vertical_focus) for point in focus_points]
    return _build_axis_expression(values, fallback_focus, "ih", "oh")


def build_keyframe_expression(
    keyframes: list[CropKeyframe],
    axis: str,
    fallback: float,
    input_dimension: str,
    output_dimension: str,
) -> str:
    values = [
        (point.time, point.x if axis == "x" else point.y)
        for point in sorted(keyframes, key=lambda item: item.time)
    ]
    return _build_axis_expression(values, fallback, input_dimension, output_dimension)


def _build_axis_expression(
    values: list[tuple[float, float]],
    fallback: float,
    input_dimension: str,
    output_dimension: str,
) -> str:
    """Build a clamped piecewise-linear crop position evaluated every frame."""
    fallback = max(0.0, min(float(fallback), 1.0))
    cleaned = [
        (max(float(time), 0.0), max(0.0, min(float(value), 1.0)))
        for time, value in values
    ]
    if len(cleaned) < 2:
        focus = cleaned[0][1] if cleaned else fallback
        return f"({input_dimension}-{output_dimension})*{focus:.6f}"

    expression = f"{cleaned[-1][1]:.6f}"
    for current, following in reversed(list(zip(cleaned, cleaned[1:]))):
        duration = max(following[0] - current[0], 0.001)
        slope = following[1] - current[1]
        linear = (
            f"{current[1]:.6f}+({slope:.6f})*"
            f"(t-{current[0]:.3f})/{duration:.3f}"
        )
        expression = f"if(lt(t,{following[0]:.3f}),{linear},{expression})"
    # Commas belong to expression functions, not the filter chain.
    escaped_expression = expression.replace(",", "\\,")
    return f"({input_dimension}-{output_dimension})*({escaped_expression})"


def _render(
    source_path: Path,
    out_path: Path,
    start: float,
    end: float,
    *,
    output_format: OutputFormat,
    crop_x: float,
    crop_y: float,
    crop_zoom: float,
    crop_keyframes: list[CropKeyframe],
    ass_path: Path | None,
    reframe_mode: ReframeMode,
    smart_analysis: ReframeAnalysis | None = None,
    smart_analysis_offset: float = 0.0,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(end - start, 0.5)
    args = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start),
        "-i",
        str(source_path),
        "-t",
        str(duration),
    ]
    focus_points: list[FocusPoint] = []
    smart_zoom = crop_zoom
    if OutputFormat(output_format) == OutputFormat.VERTICAL and ReframeMode(reframe_mode) == ReframeMode.AUTO:
        analysis = smart_analysis or track_subject(source_path, start, end)
        focus_points = (
            slice_focus_points(
                analysis.points,
                smart_analysis_offset,
                smart_analysis_offset + duration,
            )
            if smart_analysis
            else analysis.points
        )
        smart_zoom = analysis.zoom
    video_filter = _video_filter(
        output_format,
        crop_x,
        crop_y,
        smart_zoom,
        ass_path,
        focus_points,
        crop_keyframes if ReframeMode(reframe_mode) == ReframeMode.MANUAL else [],
    )
    if video_filter:
        args.extend(["-vf", video_filter])
    args.extend(
        [
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )
    _run(args, "rendering this clip")
    return out_path


def cut_clip(
    source_path: Path,
    out_path: Path,
    start: float,
    end: float,
    output_format: OutputFormat = OutputFormat.ORIGINAL,
    crop_x: float = 0.5,
    crop_y: float = 0.5,
    crop_zoom: float = 1.0,
    crop_keyframes: list[CropKeyframe] | None = None,
    reframe_mode: ReframeMode = ReframeMode.AUTO,
    smart_analysis: ReframeAnalysis | None = None,
    smart_analysis_offset: float = 0.0,
) -> Path:
    return _render(
        source_path,
        out_path,
        start,
        end,
        output_format=output_format,
        crop_x=crop_x,
        crop_y=crop_y,
        crop_zoom=crop_zoom,
        crop_keyframes=crop_keyframes or [],
        ass_path=None,
        reframe_mode=reframe_mode,
        smart_analysis=smart_analysis,
        smart_analysis_offset=smart_analysis_offset,
    )


def cut_and_caption(
    source_path: Path,
    out_path: Path,
    start: float,
    end: float,
    ass_path: Path,
    output_format: OutputFormat = OutputFormat.ORIGINAL,
    crop_x: float = 0.5,
    crop_y: float = 0.5,
    crop_zoom: float = 1.0,
    crop_keyframes: list[CropKeyframe] | None = None,
    reframe_mode: ReframeMode = ReframeMode.AUTO,
    smart_analysis: ReframeAnalysis | None = None,
    smart_analysis_offset: float = 0.0,
) -> Path:
    return _render(
        source_path,
        out_path,
        start,
        end,
        output_format=output_format,
        crop_x=crop_x,
        crop_y=crop_y,
        crop_zoom=crop_zoom,
        crop_keyframes=crop_keyframes or [],
        ass_path=ass_path,
        reframe_mode=reframe_mode,
        smart_analysis=smart_analysis,
        smart_analysis_offset=smart_analysis_offset,
    )


def extract_thumbnails(
    source_path: Path,
    out_dir: Path,
    interval_seconds: float = 2.0,
    *,
    start: float = 0.0,
    end: float | None = None,
) -> list[Path]:
    existing = sorted(out_dir.glob("frame_*.jpg")) if out_dir.exists() else []
    if existing:
        return existing
    out_dir.mkdir(parents=True, exist_ok=True)
    fps = 1 / interval_seconds
    args = [
        "ffmpeg",
        "-y",
        "-ss",
        str(max(start, 0.0)),
        "-i",
        str(source_path),
    ]
    if end is not None:
        args.extend(["-t", str(max(end - start, 0.1))])
    args.extend(
        [
            "-vf",
            f"fps={fps},scale=200:-1",
            "-q:v",
            "4",
            str(out_dir / "frame_%04d.jpg"),
        ]
    )
    _run(args, "extracting timeline thumbnails")
    return sorted(out_dir.glob("frame_*.jpg"))
