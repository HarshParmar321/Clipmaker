from __future__ import annotations

import mimetypes
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from config import (
    FAILED_JOB_TTL_HOURS,
    FINAL_CLIP_TTL_DAYS,
    JOBS_DIR,
    SOURCE_VIDEO_TTL_HOURS,
    THUMBNAIL_TTL_HOURS,
)
from services import caption_burner, video_cutter
from services.auth import AuthenticatedUser, current_user
from services.exceptions import VideoDownloadError
from services.face_tracker import track_subject
from services.gaming_layout import FacecamRegion, detect_facecam_region
from services.job_store import (
    ACTIVE_JOB_STATUSES,
    ArtifactKind,
    Job,
    JobStatus,
    create_job,
    find_artifact,
    get_job,
    mark_artifact_deleted,
    register_artifact,
    set_job_artifact_expiry,
    update_job,
)
from services.media_storage import (
    UploadTooLargeError,
    UnsupportedVideoError,
    media_storage,
)
from services.pipeline import run_pipeline
from services.render_settings import (
    CaptionPosition,
    CaptionSize,
    CaptionStyle,
    ContentMode,
    FacecamCorner,
    LayoutMode,
    OutputFormat,
    ReframeMode,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
MEDIA_CHUNK_BYTES = 1024 * 1024
RECOVERABLE_SAVED_STAGES = {JobStatus.DETECTING_HIGHLIGHTS.value}


class CreateJobFromUrl(BaseModel):
    url: HttpUrl
    creative_prompt: str | None = Field(default=None, max_length=1000)
    output_format: OutputFormat = OutputFormat.VERTICAL
    caption_style: CaptionStyle = CaptionStyle.BOLD
    content_mode: ContentMode = ContentMode.GENERAL
    target_clip_count: int = Field(default=5, ge=1, le=10)
    min_clip_duration: int = Field(default=20, ge=5, le=90)
    max_clip_duration: int = Field(default=60, ge=10, le=120)

    @field_validator("url")
    @classmethod
    def youtube_only(cls, value: HttpUrl) -> HttpUrl:
        hostname = (value.host or "").lower()
        allowed = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
        if hostname not in allowed:
            raise ValueError("Only YouTube links are supported right now")
        return value

    @field_validator("creative_prompt")
    @classmethod
    def clean_prompt(cls, value: str | None) -> str | None:
        cleaned = value.strip() if value else None
        return cleaned or None

    @model_validator(mode="after")
    def valid_duration_range(self):
        if self.max_clip_duration <= self.min_clip_duration:
            raise ValueError("Maximum clip duration must be longer than the minimum")
        return self


class CaptionWordEdit(BaseModel):
    word: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    break_after: bool = False
    join_next: bool = False

    @field_validator("word")
    @classmethod
    def clean_word(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Caption words cannot be blank")
        return cleaned

    @model_validator(mode="after")
    def valid_timing(self):
        if self.end <= self.start:
            raise ValueError("Caption word end must be after its start")
        return self


class ReframeKeyframe(BaseModel):
    time: float = Field(ge=0)
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)


class FacecamRegionPayload(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0.02, le=1)
    height: float = Field(gt=0.02, le=1)

    @model_validator(mode="after")
    def stays_inside_source(self):
        if self.x + self.width > 1.001 or self.y + self.height > 1.001:
            raise ValueError("Facecam crop must stay inside the source video")
        return self


class RenderClipRequest(BaseModel):
    start: float
    end: float
    captions: bool = True
    output_format: OutputFormat = OutputFormat.VERTICAL
    caption_style: CaptionStyle = CaptionStyle.BOLD
    caption_position: CaptionPosition = CaptionPosition.BOTTOM
    caption_size: CaptionSize = CaptionSize.MEDIUM
    caption_text: str | None = Field(default=None, max_length=5000)
    caption_words: list[CaptionWordEdit] | None = Field(default=None, max_length=1200)
    crop_x: float = Field(default=0.5, ge=0, le=1)
    crop_y: float = Field(default=0.5, ge=0, le=1)
    crop_zoom: float = Field(default=1.0, ge=1, le=3)
    crop_keyframes: list[ReframeKeyframe] = Field(default_factory=list, max_length=120)
    reframe_mode: ReframeMode = ReframeMode.AUTO
    layout_mode: LayoutMode = LayoutMode.SMART
    facecam_corner: FacecamCorner = FacecamCorner.AUTO
    facecam_region: FacecamRegionPayload | None = None

    @model_validator(mode="after")
    def valid_caption_timeline(self):
        render_duration = self.end - self.start
        if self.caption_words is not None:
            previous_start = -1.0
            for word in self.caption_words:
                if word.end > render_duration + 0.1:
                    raise ValueError("Caption timing must stay inside the trimmed clip")
                if word.start < previous_start:
                    raise ValueError("Caption words must be ordered by start time")
                previous_start = word.start
        previous_keyframe = -1.0
        for keyframe in self.crop_keyframes:
            if keyframe.time > render_duration + 0.1:
                raise ValueError("Reframe keyframes must stay inside the trimmed clip")
            if keyframe.time <= previous_keyframe:
                raise ValueError("Reframe keyframes must be strictly ordered")
            previous_keyframe = keyframe.time
        return self


def _check_access(job: Job, user: AuthenticatedUser | None) -> None:
    if job.user_id and (user is None or job.user_id != user.id):
        raise HTTPException(status_code=404, detail="Job not found")


def _get_accessible_job(job_id: str, user: AuthenticatedUser | None) -> Job:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _check_access(job, user)
    return job


def _media_url(job_id: str, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(media_storage.job_dir(job_id).resolve())
    except ValueError as exc:
        raise RuntimeError("Media path is outside its job directory") from exc
    return f"/api/jobs/{job_id}/media/{relative.as_posix()}"


def _parse_byte_range(range_header: str, file_size: int) -> tuple[int, int]:
    """Parse one RFC 7233 byte range and return inclusive start/end offsets."""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not match or file_size <= 0:
        raise ValueError("Invalid byte range")
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        raise ValueError("Invalid byte range")
    if not start_text:
        suffix_length = int(end_text)
        if suffix_length <= 0:
            raise ValueError("Invalid byte range")
        start = max(file_size - suffix_length, 0)
        end = file_size - 1
    else:
        start = int(start_text)
        end = int(end_text) if end_text else file_size - 1
    if start >= file_size or end < start:
        raise ValueError("Unsatisfiable byte range")
    return start, min(end, file_size - 1)


def _iter_file_range(path: Path, start: int, end: int):
    with path.open("rb") as source:
        source.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = source.read(min(MEDIA_CHUNK_BYTES, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _media_file_response(path: Path, media_type: str, range_header: str | None):
    file_size = path.stat().st_size
    common_headers = {"Accept-Ranges": "bytes"}
    if not range_header:
        return FileResponse(path, media_type=media_type, headers=common_headers)
    try:
        start, end = _parse_byte_range(range_header, file_size)
    except (TypeError, ValueError):
        return Response(
            status_code=416,
            headers={**common_headers, "Content-Range": f"bytes */{file_size}"},
        )
    return StreamingResponse(
        _iter_file_range(path, start, end),
        status_code=206,
        media_type=media_type,
        headers={
            **common_headers,
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(end - start + 1),
        },
    )


def _clip_words(job: Job, clip) -> list[dict]:
    return [
        word
        for word in (job.transcript or [])
        if float(word["end"]) >= clip.start and float(word["start"]) <= clip.end
    ]


def _serialize_clip(job: Job, clip) -> dict:
    clip_words = _clip_words(job, clip)
    data = clip.model_dump(exclude={"file_path"})
    data["caption_text"] = " ".join(str(word["word"]) for word in clip_words)
    data["caption_words"] = [
        {
            "word": str(word["word"]),
            "start": max(float(word["start"]) - clip.start, 0),
            "end": max(float(word["end"]) - clip.start, 0),
            "break_after": bool(word.get("break_after", False)),
            "join_next": bool(word.get("join_next", False)),
        }
        for word in clip_words
    ]
    data["preview_url"] = _media_url(job.id, Path(clip.file_path)) if clip.file_path else None
    data["download_url"] = f"/api/jobs/{job.id}/clips/{clip.id}/download"
    data["thumbnails_url"] = f"/api/jobs/{job.id}/clips/{clip.id}/thumbnails"
    data["editor_preview_url"] = f"/api/jobs/{job.id}/clips/{clip.id}/editor-preview"
    data["reframe_analysis_url"] = f"/api/jobs/{job.id}/clips/{clip.id}/reframe-analysis"
    data["gaming_analysis_url"] = f"/api/jobs/{job.id}/clips/{clip.id}/gaming-analysis"
    data["render_url"] = f"/api/jobs/{job.id}/clips/{clip.id}/render"
    return data


def _serialize_progress(job: Job) -> dict:
    return {
        "percent": job.progress_percent,
        "message": job.progress_message,
        "current_stage": job.current_stage,
        "processed_clips": job.processed_clips,
        "total_clips": job.total_clips,
        "heartbeat_at": job.heartbeat_at,
        "failed_stage": job.failed_stage,
        "retryable": job.retryable,
        "cancel_requested_at": job.cancel_requested_at,
        "created_at": job.created_at,
    }


def _can_retry_job(job: Job) -> bool:
    return (
        job.status in {JobStatus.FAILED, JobStatus.CANCELLED}
        and (
            job.retryable
            or job.failed_stage is None
            or job.failed_stage in RECOVERABLE_SAVED_STAGES
        )
    )


def _editor_preview_id(clip_id: str) -> str:
    """Return a stable UUID for the reusable source-aspect Studio preview."""
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"clipmaker:editor-preview:source-v2:{clip_id}",
        )
    )


@router.post("")
def create_job_from_url(
    payload: CreateJobFromUrl,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser | None = Depends(current_user),
):
    job = create_job(
        "url",
        str(payload.url),
        user.id if user else None,
        payload.creative_prompt,
        payload.output_format,
        payload.caption_style,
        payload.target_clip_count,
        payload.min_clip_duration,
        payload.max_clip_duration,
        payload.content_mode,
    )
    background_tasks.add_task(run_pipeline, job)
    return {"job_id": job.id, "status": job.status}


@router.post("/upload")
def create_job_from_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    creative_prompt: str | None = Form(default=None, max_length=1000),
    output_format: OutputFormat = Form(default=OutputFormat.VERTICAL),
    caption_style: CaptionStyle = Form(default=CaptionStyle.BOLD),
    content_mode: ContentMode = Form(default=ContentMode.GENERAL),
    target_clip_count: int = Form(default=5, ge=1, le=10),
    min_clip_duration: int = Form(default=20, ge=5, le=90),
    max_clip_duration: int = Form(default=60, ge=10, le=120),
    user: AuthenticatedUser | None = Depends(current_user),
):
    if max_clip_duration <= min_clip_duration:
        raise HTTPException(
            status_code=422,
            detail="Maximum clip duration must be longer than the minimum",
        )
    job = create_job(
        "upload",
        "",
        user.id if user else None,
        creative_prompt.strip() or None if creative_prompt else None,
        output_format,
        caption_style,
        target_clip_count,
        min_clip_duration,
        max_clip_duration,
        content_mode,
    )
    try:
        source_path, size_bytes = media_storage.save_upload(job.id, file.filename, file.file)
        job.source_value = str(source_path)
        update_job(job)
        register_artifact(
            job_id=job.id,
            kind=ArtifactKind.SOURCE,
            local_path=str(source_path),
            size_bytes=size_bytes,
            ttl=timedelta(hours=SOURCE_VIDEO_TTL_HOURS),
        )
    except UnsupportedVideoError as exc:
        job.status = JobStatus.FAILED
        job.error = str(exc)
        update_job(job)
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except UploadTooLargeError as exc:
        job.status = JobStatus.FAILED
        job.error = str(exc)
        update_job(job)
        set_job_artifact_expiry(job.id, timedelta(hours=FAILED_JOB_TTL_HOURS))
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    finally:
        file.file.close()

    background_tasks.add_task(run_pipeline, job)
    return {"job_id": job.id, "status": job.status}


@router.get("/{job_id}")
def get_job_status(
    job_id: str,
    user: AuthenticatedUser | None = Depends(current_user),
):
    job = _get_accessible_job(job_id, user)
    return {
        "job_id": job.id,
        "status": job.status,
        "error": job.error,
        "progress": _serialize_progress(job),
        "can_cancel": job.status in ACTIVE_JOB_STATUSES and not job.cancel_requested_at,
        "can_retry": _can_retry_job(job),
        "settings": {
            "creative_prompt": job.creative_prompt,
            "output_format": job.output_format,
            "caption_style": job.caption_style,
            "content_mode": job.content_mode,
            "target_clip_count": job.target_clip_count,
            "min_clip_duration": job.min_clip_duration,
            "max_clip_duration": job.max_clip_duration,
        },
        "clips": [_serialize_clip(job, clip) for clip in job.clips],
    }


@router.post("/{job_id}/cancel", status_code=202)
def cancel_job(
    job_id: str,
    user: AuthenticatedUser | None = Depends(current_user),
):
    job = _get_accessible_job(job_id, user)
    if job.status not in ACTIVE_JOB_STATUSES:
        raise HTTPException(status_code=409, detail="Only an active job can be cancelled")
    if not job.cancel_requested_at:
        job.cancel_requested_at = datetime.now(timezone.utc)
        job.progress_message = "Cancellation requested—finishing the current safe step…"
        job.heartbeat_at = datetime.now(timezone.utc)
        update_job(job)
    return {
        "job_id": job.id,
        "status": job.status,
        "message": job.progress_message,
    }


@router.post("/{job_id}/retry", status_code=202)
def retry_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser | None = Depends(current_user),
):
    job = _get_accessible_job(job_id, user)
    if job.status not in {JobStatus.FAILED, JobStatus.CANCELLED}:
        raise HTTPException(status_code=409, detail="Only a failed or cancelled job can be retried")
    if not _can_retry_job(job):
        raise HTTPException(
            status_code=409,
            detail=job.error or "This failure needs new input instead of a retry",
        )
    if job.source_type == "upload":
        source_candidates = [job.video_path, job.source_value]
        if not any(value and Path(value).is_file() for value in source_candidates):
            raise HTTPException(
                status_code=410,
                detail="The uploaded source has expired. Please upload the video again.",
            )

    job.status = JobStatus.QUEUED
    job.current_stage = JobStatus.QUEUED.value
    job.progress_percent = 0
    job.progress_message = "Queued for retry…"
    job.processed_clips = sum(
        1 for clip in job.clips if clip.file_path and Path(clip.file_path).is_file()
    )
    job.total_clips = max(job.total_clips, job.processed_clips)
    job.error = None
    job.failed_stage = None
    job.retryable = False
    job.cancel_requested_at = None
    job.heartbeat_at = datetime.now(timezone.utc)
    update_job(job)
    background_tasks.add_task(run_pipeline, job)
    return {"job_id": job.id, "status": job.status, "message": job.progress_message}


@router.get("/{job_id}/media/{relative_path:path}")
def serve_job_media(
    job_id: str,
    relative_path: str,
    request: Request,
    user: AuthenticatedUser | None = Depends(current_user),
):
    job = _get_accessible_job(job_id, user)
    root = media_storage.job_dir(job.id).resolve()
    path = (root / relative_path).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Media not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return _media_file_response(path, media_type, request.headers.get("range"))


@router.get("/{job_id}/clips/{clip_id}/download")
def download_clip(
    job_id: str,
    clip_id: str,
    user: AuthenticatedUser | None = Depends(current_user),
):
    job = _get_accessible_job(job_id, user)
    clip = next((item for item in job.clips if item.id == clip_id), None)
    if not clip or not clip.file_path:
        raise HTTPException(status_code=404, detail="Clip not found")
    path = Path(clip.file_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Clip has expired")
    safe_title = "".join(character for character in clip.title if character.isalnum() or character in " -_")
    return FileResponse(path, media_type="video/mp4", filename=f"{safe_title.strip() or 'clip'}.mp4")


@router.get("/{job_id}/clips/{clip_id}/thumbnails")
def get_clip_thumbnails(
    job_id: str,
    clip_id: str,
    user: AuthenticatedUser | None = Depends(current_user),
):
    job = _get_accessible_job(job_id, user)
    clip = next((item for item in job.clips if item.id == clip_id), None)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if not job.video_path or not Path(job.video_path).exists():
        raise HTTPException(status_code=410, detail="Original source video has expired")
    source_path = Path(job.video_path)
    preview_id = _editor_preview_id(clip.id)
    out_dir = media_storage.thumbnails_dir(job.id, preview_id)
    try:
        thumbnails = video_cutter.extract_thumbnails(
            source_path,
            out_dir,
            interval_seconds=1.0,
            start=clip.start,
            end=clip.end,
        )
    except VideoDownloadError as exc:
        raise HTTPException(status_code=500, detail=exc.user_message) from exc

    existing_artifact = find_artifact(job.id, ArtifactKind.THUMBNAILS, clip.id)
    if existing_artifact and Path(existing_artifact.local_path).resolve() != out_dir.resolve():
        mark_artifact_deleted(existing_artifact.id)
        existing_artifact = None
    if not existing_artifact:
        register_artifact(
            job_id=job.id,
            clip_id=clip.id,
            kind=ArtifactKind.THUMBNAILS,
            local_path=str(out_dir),
            size_bytes=media_storage.size(out_dir),
            ttl=timedelta(hours=THUMBNAIL_TTL_HOURS),
        )
    return {
        "thumbnails": [_media_url(job.id, path) for path in thumbnails],
        "clip_duration": clip.end - clip.start,
    }


@router.get("/{job_id}/clips/{clip_id}/editor-preview")
def get_editor_preview(
    job_id: str,
    clip_id: str,
    user: AuthenticatedUser | None = Depends(current_user),
):
    """Create a clean clip-only preview so Studio never seeks through the full source."""
    job = _get_accessible_job(job_id, user)
    clip = next((item for item in job.clips if item.id == clip_id), None)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    preview_id = _editor_preview_id(clip.id)
    preview_path = media_storage.output_path(job.id, preview_id)
    if not preview_path.exists():
        if not job.video_path or not Path(job.video_path).exists():
            raise HTTPException(status_code=410, detail="Original source video has expired")
        try:
            video_cutter.cut_clip(
                Path(job.video_path),
                preview_path,
                clip.start,
                clip.end,
                output_format=OutputFormat.ORIGINAL,
                reframe_mode=ReframeMode.MANUAL,
            )
        except VideoDownloadError as exc:
            raise HTTPException(status_code=500, detail=exc.user_message) from exc
        register_artifact(
            job_id=job.id,
            clip_id=clip.id,
            kind=ArtifactKind.EDITED_CLIP,
            local_path=str(preview_path),
            size_bytes=media_storage.size(preview_path),
            ttl=timedelta(days=FINAL_CLIP_TTL_DAYS),
        )

    media = video_cutter.probe_media(preview_path)
    return {
        "preview_url": _media_url(job.id, preview_path),
        "clip_duration": clip.end - clip.start,
        "source_width": media["width"],
        "source_height": media["height"],
    }


@router.get("/{job_id}/clips/{clip_id}/reframe-analysis")
def get_reframe_analysis(
    job_id: str,
    clip_id: str,
    user: AuthenticatedUser | None = Depends(current_user),
):
    """Return the exact Smart Reframe path used by a vertical export."""
    job = _get_accessible_job(job_id, user)
    clip = next((item for item in job.clips if item.id == clip_id), None)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if not job.video_path or not Path(job.video_path).exists():
        raise HTTPException(status_code=410, detail="Original source video has expired")
    analysis = track_subject(Path(job.video_path), clip.start, clip.end)
    if analysis.detection_ratio >= 0.7:
        quality = "strong"
    elif analysis.detection_ratio >= 0.35:
        quality = "good"
    elif analysis.points:
        quality = "limited"
    else:
        quality = "centered-fallback"
    return {
        "points": [
            {
                "time": point.time,
                "x": point.focus,
                "y": point.vertical_focus,
                "confidence": point.confidence,
                "scene_cut": point.scene_cut,
            }
            for point in analysis.points
        ],
        "zoom": analysis.zoom,
        "detection_ratio": round(analysis.detection_ratio, 3),
        "detector": analysis.detector,
        "scene_cuts": analysis.scene_cuts,
        "subject_switches": analysis.subject_switches,
        "quality": quality,
        "message": (
            "Smart Reframe is ready. Review the path, or choose Manual to override it."
            if analysis.points
            else "No reliable subject was found, so this clip will use a safe centered crop."
        ),
    }


@router.get("/{job_id}/clips/{clip_id}/gaming-analysis")
def get_gaming_analysis(
    job_id: str,
    clip_id: str,
    corner: FacecamCorner = FacecamCorner.AUTO,
    user: AuthenticatedUser | None = Depends(current_user),
):
    """Locate the stable corner facecam used by Gameplay + Reaction exports."""
    job = _get_accessible_job(job_id, user)
    clip = next((item for item in job.clips if item.id == clip_id), None)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if not job.video_path or not Path(job.video_path).exists():
        raise HTTPException(status_code=410, detail="Original source video has expired")
    analysis = detect_facecam_region(Path(job.video_path), clip.start, clip.end, corner)
    return {
        "region": {
            "x": round(analysis.region.x, 5),
            "y": round(analysis.region.y, 5),
            "width": round(analysis.region.width, 5),
            "height": round(analysis.region.height, 5),
        },
        "confidence": round(analysis.confidence, 3),
        "detection_ratio": round(analysis.detection_ratio, 3),
        "detector": analysis.detector,
        "corner": analysis.corner,
        "used_fallback": analysis.used_fallback,
        "message": (
            "OBS facecam detected. Choose another corner if the preview is wrong."
            if not analysis.used_fallback
            else "No stable facecam was detected, so the selected corner is being used."
        ),
    }


@router.post("/{job_id}/clips/{clip_id}/render")
def render_clip(
    job_id: str,
    clip_id: str,
    payload: RenderClipRequest,
    user: AuthenticatedUser | None = Depends(current_user),
):
    job = _get_accessible_job(job_id, user)
    clip = next((item for item in job.clips if item.id == clip_id), None)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if not job.video_path or not Path(job.video_path).exists():
        raise HTTPException(status_code=410, detail="Original source video has expired")

    clip_duration = clip.end - clip.start
    if payload.start < 0 or payload.end > clip_duration or payload.end <= payload.start:
        raise HTTPException(status_code=400, detail="Trim points must stay inside the original clip")

    absolute_start = clip.start + payload.start
    absolute_end = clip.start + payload.end
    render_id = str(uuid.uuid4())
    final_path = media_storage.output_path(job.id, render_id)
    ass_path = media_storage.subtitle_path(job.id, render_id)
    subtitle_artifact_id: str | None = None
    smart_analysis = None
    gaming_analysis = None
    gaming_layout = (
        payload.output_format == OutputFormat.VERTICAL
        and payload.layout_mode == LayoutMode.GAMEPLAY_REACTION
    )
    if (
        not gaming_layout
        and
        payload.output_format == OutputFormat.VERTICAL
        and payload.reframe_mode == ReframeMode.AUTO
    ):
        # Analyse the whole suggested clip once. Trimmed renders slice this
        # cached path so Studio preview and FFmpeg use identical framing.
        smart_analysis = track_subject(Path(job.video_path), clip.start, clip.end)
    if gaming_layout and payload.facecam_region is None:
        gaming_analysis = detect_facecam_region(
            Path(job.video_path), clip.start, clip.end, payload.facecam_corner
        )
    facecam_region = (
        FacecamRegion(**payload.facecam_region.model_dump())
        if payload.facecam_region is not None
        else gaming_analysis.region if gaming_analysis else None
    )
    caption_position = (
        CaptionPosition.GAMING
        if gaming_layout and payload.caption_position == CaptionPosition.BOTTOM
        else payload.caption_position
    )
    try:
        if payload.captions and job.transcript:
            subtitle_artifact = register_artifact(
                job_id=job.id,
                kind=ArtifactKind.SUBTITLE_TEMP,
                local_path=str(ass_path),
                ttl=timedelta(hours=FAILED_JOB_TTL_HOURS),
            )
            subtitle_artifact_id = subtitle_artifact.id
            if payload.caption_words is not None:
                words = [
                    {
                        "word": word.word,
                        "start": absolute_start + word.start,
                        "end": absolute_start + word.end,
                        "break_after": word.break_after,
                        "join_next": word.join_next,
                    }
                    for word in payload.caption_words
                ]
            else:
                words = [
                    word
                    for word in job.transcript
                    if float(word["end"]) >= absolute_start
                    and float(word["start"]) <= absolute_end
                ]
                words = caption_burner.retime_caption_text(
                    words, payload.caption_text, absolute_start, absolute_end
                )
            frame_width, frame_height = video_cutter.render_dimensions(
                Path(job.video_path), payload.output_format
            )
            caption_burner.build_ass_file(
                words,
                ass_path,
                clip_start_offset=absolute_start,
                frame_width=frame_width,
                frame_height=frame_height,
                style=payload.caption_style,
                position=caption_position,
                size=payload.caption_size,
            )
            video_cutter.cut_and_caption(
                Path(job.video_path),
                final_path,
                absolute_start,
                absolute_end,
                ass_path,
                output_format=payload.output_format,
                crop_x=payload.crop_x,
                crop_y=payload.crop_y,
                crop_zoom=payload.crop_zoom,
                crop_keyframes=[
                    video_cutter.CropKeyframe(item.time, item.x, item.y)
                    for item in payload.crop_keyframes
                ],
                reframe_mode=payload.reframe_mode,
                smart_analysis=smart_analysis,
                smart_analysis_offset=payload.start,
                layout_mode=payload.layout_mode,
                facecam_region=facecam_region,
                facecam_corner=payload.facecam_corner,
            )
        else:
            video_cutter.cut_clip(
                Path(job.video_path),
                final_path,
                absolute_start,
                absolute_end,
                output_format=payload.output_format,
                crop_x=payload.crop_x,
                crop_y=payload.crop_y,
                crop_zoom=payload.crop_zoom,
                crop_keyframes=[
                    video_cutter.CropKeyframe(item.time, item.x, item.y)
                    for item in payload.crop_keyframes
                ],
                reframe_mode=payload.reframe_mode,
                smart_analysis=smart_analysis,
                smart_analysis_offset=payload.start,
                layout_mode=payload.layout_mode,
                facecam_region=facecam_region,
                facecam_corner=payload.facecam_corner,
            )
    except VideoDownloadError as exc:
        raise HTTPException(status_code=500, detail=exc.user_message) from exc
    finally:
        try:
            media_storage.delete(ass_path)
        finally:
            if subtitle_artifact_id:
                mark_artifact_deleted(subtitle_artifact_id)

    register_artifact(
        job_id=job.id,
        clip_id=clip.id,
        kind=ArtifactKind.EDITED_CLIP,
        local_path=str(final_path),
        size_bytes=media_storage.size(final_path),
        ttl=timedelta(days=FINAL_CLIP_TTL_DAYS),
    )
    media_url = _media_url(job.id, final_path)
    return {
        "preview_url": media_url,
        "download_url": media_url,
        "output_format": payload.output_format,
        "caption_style": payload.caption_style,
        "caption_size": payload.caption_size,
        "caption_position": caption_position,
        "reframe_mode": payload.reframe_mode,
        "layout_mode": payload.layout_mode,
        "crop_zoom": payload.crop_zoom,
        "crop_keyframes": len(payload.crop_keyframes),
        "smart_reframe": (
            {
                "quality": round(smart_analysis.detection_ratio, 3),
                "zoom": smart_analysis.zoom,
                "scene_cuts": smart_analysis.scene_cuts,
                "subject_switches": smart_analysis.subject_switches,
            }
            if smart_analysis
            else None
        ),
        "gaming_reframe": (
            {
                "corner": gaming_analysis.corner,
                "confidence": round(gaming_analysis.confidence, 3),
                "used_fallback": gaming_analysis.used_fallback,
            }
            if gaming_analysis
            else None
        ),
    }
