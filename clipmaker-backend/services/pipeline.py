"""Orchestrates one durable, job-scoped clip-generation pipeline."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TypeVar

import httpx

from config import (
    FAILED_JOB_TTL_HOURS,
    FINAL_CLIP_TTL_DAYS,
    MAX_VIDEO_DURATION_SECONDS,
    SOURCE_VIDEO_TTL_HOURS,
)
from services import caption_burner, downloader, highlight_detector, transcriber, video_cutter
from services.exceptions import VideoDownloadError
from services.job_store import (
    ArtifactKind,
    Clip,
    Job,
    JobStatus,
    find_artifact,
    get_job,
    mark_artifact_deleted,
    register_artifact,
    set_job_artifact_expiry,
    update_job,
)
from services.media_storage import media_storage
from services.render_settings import CaptionPosition

logger = logging.getLogger("clipmaker.pipeline")
T = TypeVar("T")


class PipelineCancelled(Exception):
    """Raised at a safe checkpoint after the user requests cancellation."""


def _delete_artifact_file(path: Path, artifact_id: str | None) -> None:
    try:
        media_storage.delete(path)
    finally:
        if artifact_id:
            mark_artifact_deleted(artifact_id)


def _existing_source_video(job: Job) -> Path | None:
    """Return a previously saved source so retried jobs can resume."""
    if job.video_path and Path(job.video_path).is_file():
        return Path(job.video_path)
    if job.source_type == "upload" and job.source_value and Path(job.source_value).is_file():
        return Path(job.source_value)
    source_dir = media_storage.source_dir(job.id)
    if not source_dir.exists():
        return None
    return next((path for path in sorted(source_dir.iterdir()) if path.is_file()), None)


def _checkpoint(
    job: Job,
    status: JobStatus,
    progress_percent: int,
    message: str,
    *,
    processed_clips: int | None = None,
    total_clips: int | None = None,
    check_cancel: bool = True,
) -> None:
    """Persist visible progress and cooperatively stop at safe boundaries."""
    latest = get_job(job.id)
    if latest and latest.cancel_requested_at:
        job.cancel_requested_at = latest.cancel_requested_at
        if check_cancel:
            raise PipelineCancelled

    job.status = status
    job.current_stage = status.value
    job.progress_percent = max(0, min(int(progress_percent), 100))
    job.progress_message = message
    if processed_clips is not None:
        job.processed_clips = max(0, processed_clips)
    if total_clips is not None:
        job.total_clips = max(0, total_clips)
    job.heartbeat_at = datetime.now(timezone.utc)
    update_job(job)


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TransportError, TimeoutError, ConnectionError)):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code in {408, 409, 429} or (isinstance(status_code, int) and status_code >= 500):
        return True
    detail = str(exc).lower()
    if status_code == 400 and (
        "json_validate_failed" in detail or "failed to validate json" in detail
    ):
        return True
    name = type(exc).__name__.lower()
    return any(token in name for token in ("connection", "timeout", "ratelimit"))


def _retry_operation(
    job: Job,
    operation: Callable[[], T],
    *,
    status: JobStatus,
    progress_percent: int,
    message: str,
    attempts: int = 3,
) -> T:
    """Retry temporary Groq/network failures with short exponential backoff."""
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt == attempts or not _is_transient_error(exc):
                raise
            delay = 1.5 * (2 ** (attempt - 1))
            logger.warning(
                "[%s] temporary %s failure; retry %d/%d in %.1fs: %s",
                job.id,
                status.value,
                attempt + 1,
                attempts,
                delay,
                exc,
            )
            _checkpoint(
                job,
                status,
                progress_percent,
                f"Temporary connection issue—retrying {message.lower()} ({attempt + 1}/{attempts})…",
            )
            time.sleep(delay)
    raise RuntimeError("Retry loop ended unexpectedly")


def _matching_clip(job: Job, start: float, end: float) -> Clip | None:
    return next(
        (
            clip
            for clip in job.clips
            if abs(clip.start - start) <= 0.25
            and abs(clip.end - end) <= 0.25
            and clip.file_path
            and Path(clip.file_path).is_file()
        ),
        None,
    )


def _mark_failed(job: Job, message: str, *, retryable: bool) -> None:
    job.failed_stage = job.current_stage or job.status.value
    job.status = JobStatus.FAILED
    job.progress_message = message
    job.error = message
    job.retryable = retryable
    job.heartbeat_at = datetime.now(timezone.utc)
    update_job(job)


def run_pipeline(job: Job) -> None:
    audio_path = media_storage.audio_path(job.id)
    audio_artifact_id: str | None = None
    try:
        job.error = None
        job.failed_stage = None
        job.retryable = False
        _checkpoint(job, JobStatus.QUEUED, 1, "Preparing this job…")

        if job.source_type == "url":
            _checkpoint(job, JobStatus.DOWNLOADING, 5, "Downloading the source video…")
            video_path = _existing_source_video(job)
            if video_path:
                logger.info("[%s] reusing downloaded source: %s", job.id, video_path)
            else:
                video_path = downloader.download_video(
                    job.source_value, job.id, media_storage.source_dir(job.id)
                )
            job.video_path = str(video_path)
            _checkpoint(job, JobStatus.DOWNLOADING, 16, "Source video is ready")
            if not find_artifact(job.id, ArtifactKind.SOURCE):
                register_artifact(
                    job_id=job.id,
                    kind=ArtifactKind.SOURCE,
                    local_path=str(video_path),
                    size_bytes=media_storage.size(video_path),
                    ttl=timedelta(hours=SOURCE_VIDEO_TTL_HOURS),
                )
        else:
            video_path = _existing_source_video(job)
            if not video_path:
                raise VideoDownloadError(
                    "The uploaded source has expired. Please upload the video again.",
                    retryable=False,
                )

        duration = video_cutter.probe_duration(video_path)
        if duration > MAX_VIDEO_DURATION_SECONDS:
            raise VideoDownloadError(
                f"That video is {int(duration // 60)} minutes long. "
                f"For now, upload videos under {MAX_VIDEO_DURATION_SECONDS // 60} minutes.",
                retryable=False,
            )

        job.video_path = str(video_path)
        words = job.transcript or []
        if words:
            logger.info("[%s] reusing saved transcript", job.id)
            _checkpoint(job, JobStatus.TRANSCRIBING, 45, "Reusing the saved transcript")
        else:
            _checkpoint(job, JobStatus.TRANSCRIBING, 24, "Transcribing speech with word timing…")
            audio_artifact = register_artifact(
                job_id=job.id,
                kind=ArtifactKind.AUDIO_TEMP,
                local_path=str(audio_path),
                ttl=timedelta(hours=FAILED_JOB_TTL_HOURS),
            )
            audio_artifact_id = audio_artifact.id
            words = _retry_operation(
                job,
                lambda: transcriber.transcribe(video_path, audio_path),
                status=JobStatus.TRANSCRIBING,
                progress_percent=32,
                message="transcription",
            )
            job.transcript = words
            _delete_artifact_file(audio_path, audio_artifact_id)
            audio_artifact_id = None
            _checkpoint(job, JobStatus.TRANSCRIBING, 46, "Transcript is ready")

        _checkpoint(
            job,
            JobStatus.DETECTING_HIGHLIGHTS,
            54,
            "Scoring hooks, relevance, completeness, and shareability…",
        )
        raw_clips = _retry_operation(
            job,
            lambda: highlight_detector.detect_highlights(
                words,
                job.creative_prompt,
                target_clip_count=job.target_clip_count,
                min_clip_duration=job.min_clip_duration,
                max_clip_duration=job.max_clip_duration,
            ),
            status=JobStatus.DETECTING_HIGHLIGHTS,
            progress_percent=58,
            message="highlight selection",
        )
        clips = highlight_detector.validate_highlights(
            raw_clips,
            duration,
            words,
            target_clip_count=job.target_clip_count,
            min_clip_duration=job.min_clip_duration,
            max_clip_duration=job.max_clip_duration,
        )
        logger.info("[%s] accepted %d validated highlight(s)", job.id, len(clips))
        if not clips:
            raise VideoDownloadError(
                "No strong, self-contained moments matched these settings. "
                "Try a different creative direction or clip length.",
                retryable=False,
            )

        reusable_clips: list[Clip] = []
        for raw in clips:
            existing = _matching_clip(job, raw["start"], raw["end"])
            if existing and existing not in reusable_clips:
                reusable_clips.append(existing)
        job.clips = reusable_clips
        total_clips = len(clips)
        processed_clips = len(reusable_clips)
        _checkpoint(
            job,
            JobStatus.CUTTING_CLIPS,
            67,
            (
                f"Reusing {processed_clips} finished clip(s)…"
                if processed_clips
                else f"Preparing {total_clips} clip(s)…"
            ),
            processed_clips=processed_clips,
            total_clips=total_clips,
        )

        for index, raw in enumerate(clips, start=1):
            start, end = raw["start"], raw["end"]
            if _matching_clip(job, start, end):
                continue

            clip_id = str(uuid.uuid4())
            ass_path = media_storage.subtitle_path(job.id, clip_id)
            final_path = media_storage.output_path(job.id, clip_id)
            subtitle_artifact_id: str | None = None
            progress = 68 + round((processed_clips / total_clips) * 29)

            try:
                _checkpoint(
                    job,
                    JobStatus.BURNING_CAPTIONS,
                    progress,
                    f"Rendering clip {index} of {total_clips}…",
                    processed_clips=processed_clips,
                    total_clips=total_clips,
                )
                subtitle_artifact = register_artifact(
                    job_id=job.id,
                    kind=ArtifactKind.SUBTITLE_TEMP,
                    local_path=str(ass_path),
                    ttl=timedelta(hours=FAILED_JOB_TTL_HOURS),
                )
                subtitle_artifact_id = subtitle_artifact.id
                clip_words = [
                    word
                    for word in words
                    if float(word["end"]) >= start and float(word["start"]) <= end
                ]
                frame_width, frame_height = video_cutter.render_dimensions(
                    video_path, job.output_format
                )
                caption_burner.build_ass_file(
                    clip_words,
                    ass_path,
                    clip_start_offset=start,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    style=job.caption_style,
                    position=CaptionPosition.BOTTOM,
                )
                video_cutter.cut_and_caption(
                    video_path,
                    final_path,
                    start,
                    end,
                    ass_path,
                    output_format=job.output_format,
                )

                job.clips.append(
                    Clip(
                        id=clip_id,
                        title=raw["title"],
                        start=start,
                        end=end,
                        score=raw["score"],
                        score_breakdown=raw.get("score_breakdown") or {},
                        reason=raw["reason"],
                        file_path=str(final_path),
                        output_format=job.output_format,
                        caption_style=job.caption_style,
                    )
                )
                processed_clips += 1
                _checkpoint(
                    job,
                    JobStatus.BURNING_CAPTIONS,
                    68 + round((processed_clips / total_clips) * 29),
                    f"Finished clip {processed_clips} of {total_clips}",
                    processed_clips=processed_clips,
                    total_clips=total_clips,
                    check_cancel=False,
                )
                register_artifact(
                    job_id=job.id,
                    clip_id=clip_id,
                    kind=ArtifactKind.FINAL_CLIP,
                    local_path=str(final_path),
                    size_bytes=media_storage.size(final_path),
                    ttl=timedelta(days=FINAL_CLIP_TTL_DAYS),
                )
                if job.cancel_requested_at:
                    raise PipelineCancelled
            finally:
                _delete_artifact_file(ass_path, subtitle_artifact_id)

        job.status = JobStatus.DONE
        job.current_stage = JobStatus.DONE.value
        job.progress_percent = 100
        job.progress_message = f"Finished {len(job.clips)} clip(s)"
        job.processed_clips = len(job.clips)
        job.total_clips = len(job.clips)
        job.retryable = False
        job.cancel_requested_at = None
        job.heartbeat_at = datetime.now(timezone.utc)
        update_job(job)

    except PipelineCancelled:
        logger.info("[%s] cancellation acknowledged at a safe checkpoint", job.id)
        job.status = JobStatus.CANCELLED
        job.progress_message = "Cancelled safely. Saved work can be retried."
        job.error = None
        job.failed_stage = job.current_stage
        job.retryable = True
        job.heartbeat_at = datetime.now(timezone.utc)
        update_job(job)
    except VideoDownloadError as exc:
        logger.error("[%s] %s | detail: %s", job.id, exc.user_message, exc.technical_detail)
        _mark_failed(job, exc.user_message, retryable=exc.retryable)
        if not exc.retryable:
            set_job_artifact_expiry(job.id, timedelta(hours=FAILED_JOB_TTL_HOURS))
    except Exception:
        logger.exception("[%s] Unexpected pipeline failure", job.id)
        _mark_failed(
            job,
            "Something unexpected went wrong while processing the video.",
            retryable=True,
        )
    finally:
        if audio_artifact_id or audio_path.exists():
            _delete_artifact_file(audio_path, audio_artifact_id)
        media_storage.clear_temp(job.id)
