"""
Cuts the source video into individual clips with ffmpeg, and extracts
thumbnail frames for the frontend timeline strip.
"""
import subprocess
from pathlib import Path
from config import CLIPS_DIR
from services.exceptions import VideoDownloadError


def _run_ffmpeg(args: list[str], step_description: str) -> None:
    """Runs an ffmpeg command and raises a clean, detailed error on
    failure instead of silently swallowing ffmpeg's real stderr output."""
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise VideoDownloadError(
            user_message=f"Something went wrong while {step_description}.",
            technical_detail=f"ffmpeg exit {result.returncode}: {result.stderr[-2000:]}",
        )


def cut_clip(source_path: Path, clip_id: str, start: float, end: float) -> Path:
    out_path = CLIPS_DIR / f"{clip_id}_raw.mp4"
    duration = max(end - start, 0.5)
    _run_ffmpeg(
        [
            "ffmpeg", "-y",
            "-ss", str(start), "-i", str(source_path), "-t", str(duration),
            # re-encode (rather than -c copy) so the cut lands on an exact
            # frame instead of snapping to the nearest keyframe
            "-c:v", "libx264", "-c:a", "aac", "-preset", "ultrafast",
            str(out_path),
        ],
        step_description="cutting this clip",
    )
    return out_path


def cut_and_caption(source_path: Path, clip_id: str, start: float, end: float, ass_path: Path) -> Path:
    """Cuts a clip AND burns captions in a single ffmpeg pass.
    This is ~2x faster than cut_clip() + burn_captions() because it
    avoids encoding the video twice."""
    out_path = CLIPS_DIR / f"{clip_id}_captioned.mp4"
    duration = max(end - start, 0.5)
    escaped_ass = str(ass_path).replace("\\", "\\\\").replace(":", "\\:")
    _run_ffmpeg(
        [
            "ffmpeg", "-y",
            "-ss", str(start), "-i", str(source_path), "-t", str(duration),
            "-vf", f"ass={escaped_ass}",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            str(out_path),
        ],
        step_description="cutting and captioning this clip",
    )
    return out_path


def extract_thumbnails(source_path: Path, job_id: str, interval_seconds: float = 2.0) -> list[str]:
    """Extracts a frame every `interval_seconds` for the timeline UI."""
    out_dir = CLIPS_DIR / f"{job_id}_frames"
    out_dir.mkdir(exist_ok=True)
    fps = 1 / interval_seconds
    pattern = str(out_dir / "frame_%04d.jpg")
    _run_ffmpeg(
        [
            "ffmpeg", "-y", "-i", str(source_path),
            "-vf", f"fps={fps},scale=160:-1",
            "-q:v", "4",
            pattern,
        ],
        step_description="extracting timeline thumbnails",
    )
    return sorted(str(p) for p in out_dir.glob("frame_*.jpg"))
