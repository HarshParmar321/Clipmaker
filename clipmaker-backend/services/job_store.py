"""Job and artifact persistence with an automatic Supabase backend.

When Supabase credentials are not configured, a process-local repository keeps
the API usable for setup and tests. Adding valid credentials switches all calls
to Supabase without changing the rest of the application.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, Protocol

from pydantic import BaseModel, Field, model_validator

from config import SUPABASE_CONFIGURED, SUPABASE_SERVER_KEY, SUPABASE_URL
from services.render_settings import CaptionStyle, ContentMode, LayoutMode, OutputFormat

logger = logging.getLogger("clipmaker.job_store")


class JobStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    DETECTING_HIGHLIGHTS = "detecting_highlights"
    CUTTING_CLIPS = "cutting_clips"
    BURNING_CAPTIONS = "burning_captions"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_JOB_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.DOWNLOADING,
    JobStatus.TRANSCRIBING,
    JobStatus.DETECTING_HIGHLIGHTS,
    JobStatus.CUTTING_CLIPS,
    JobStatus.BURNING_CAPTIONS,
}


class ArtifactKind(str, Enum):
    SOURCE = "source"
    AUDIO_TEMP = "audio_temp"
    SUBTITLE_TEMP = "subtitle_temp"
    THUMBNAILS = "thumbnails"
    FINAL_CLIP = "final_clip"
    EDITED_CLIP = "edited_clip"


class Clip(BaseModel):
    id: str
    title: str
    start: float
    end: float
    score: int = Field(ge=0, le=100)
    score_breakdown: dict[str, int] = Field(default_factory=dict)
    reason: str = ""
    file_path: Optional[str] = None
    output_format: OutputFormat = OutputFormat.VERTICAL
    caption_style: CaptionStyle = CaptionStyle.BOLD
    layout_mode: LayoutMode = LayoutMode.SMART


class Job(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    status: JobStatus = JobStatus.QUEUED
    source_type: str
    source_value: str
    error: Optional[str] = None
    video_path: Optional[str] = None
    transcript: Optional[list[dict]] = None
    clips: list[Clip] = Field(default_factory=list)
    creative_prompt: Optional[str] = None
    output_format: OutputFormat = OutputFormat.VERTICAL
    caption_style: CaptionStyle = CaptionStyle.BOLD
    content_mode: ContentMode = ContentMode.GENERAL
    target_clip_count: int = Field(default=5, ge=1, le=10)
    min_clip_duration: int = Field(default=20, ge=5, le=90)
    max_clip_duration: int = Field(default=60, ge=10, le=120)
    progress_percent: int = Field(default=0, ge=0, le=100)
    progress_message: str = "Waiting to start…"
    current_stage: Optional[str] = JobStatus.QUEUED.value
    processed_clips: int = Field(default=0, ge=0)
    total_clips: int = Field(default=0, ge=0)
    heartbeat_at: Optional[datetime] = None
    failed_stage: Optional[str] = None
    retryable: bool = False
    cancel_requested_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def valid_duration_range(self):
        if self.max_clip_duration <= self.min_clip_duration:
            raise ValueError("Maximum clip duration must be longer than the minimum")
        return self


class Artifact(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    clip_id: Optional[str] = None
    kind: ArtifactKind
    local_path: str
    size_bytes: int = 0
    expires_at: datetime
    deleted_at: Optional[datetime] = None


class JobRepository(Protocol):
    def create_job(
        self,
        source_type: str,
        source_value: str,
        user_id: str | None,
        creative_prompt: str | None,
        output_format: OutputFormat,
        caption_style: CaptionStyle,
        target_clip_count: int,
        min_clip_duration: int,
        max_clip_duration: int,
        content_mode: ContentMode = ContentMode.GENERAL,
    ) -> Job: ...
    def get_job(self, job_id: str) -> Job | None: ...
    def update_job(self, job: Job) -> None: ...
    def register_artifact(self, artifact: Artifact) -> Artifact: ...
    def find_artifact(self, job_id: str, kind: ArtifactKind, clip_id: str | None = None) -> Artifact | None: ...
    def list_expired_artifacts(self, now: datetime) -> list[Artifact]: ...
    def mark_artifact_deleted(self, artifact_id: str, deleted_at: datetime) -> None: ...
    def set_job_artifact_expiry(self, job_id: str, expires_at: datetime) -> None: ...
    def recover_interrupted_jobs(self, interrupted_at: datetime) -> int: ...


class MemoryJobRepository:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.artifacts: dict[str, Artifact] = {}
        self._lock = threading.RLock()

    def create_job(
        self,
        source_type: str,
        source_value: str,
        user_id: str | None,
        creative_prompt: str | None,
        output_format: OutputFormat,
        caption_style: CaptionStyle,
        target_clip_count: int,
        min_clip_duration: int,
        max_clip_duration: int,
        content_mode: ContentMode = ContentMode.GENERAL,
    ) -> Job:
        job = Job(
            source_type=source_type,
            source_value=source_value,
            user_id=user_id,
            creative_prompt=creative_prompt,
            output_format=output_format,
            caption_style=caption_style,
            target_clip_count=target_clip_count,
            min_clip_duration=min_clip_duration,
            max_clip_duration=max_clip_duration,
            content_mode=content_mode,
        )
        with self._lock:
            self.jobs[job.id] = job.model_copy(deep=True)
        return job

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            job = self.jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def update_job(self, job: Job) -> None:
        with self._lock:
            self.jobs[job.id] = job.model_copy(deep=True)

    def register_artifact(self, artifact: Artifact) -> Artifact:
        with self._lock:
            self.artifacts[artifact.id] = artifact.model_copy(deep=True)
        return artifact

    def find_artifact(
        self, job_id: str, kind: ArtifactKind, clip_id: str | None = None
    ) -> Artifact | None:
        with self._lock:
            for artifact in self.artifacts.values():
                if (
                    artifact.job_id == job_id
                    and artifact.kind == kind
                    and artifact.clip_id == clip_id
                    and artifact.deleted_at is None
                ):
                    return artifact.model_copy(deep=True)
        return None

    def list_expired_artifacts(self, now: datetime) -> list[Artifact]:
        with self._lock:
            return [
                artifact.model_copy(deep=True)
                for artifact in self.artifacts.values()
                if artifact.deleted_at is None and artifact.expires_at <= now
            ]

    def mark_artifact_deleted(self, artifact_id: str, deleted_at: datetime) -> None:
        with self._lock:
            artifact = self.artifacts.get(artifact_id)
            if artifact:
                artifact.deleted_at = deleted_at

    def set_job_artifact_expiry(self, job_id: str, expires_at: datetime) -> None:
        with self._lock:
            for artifact in self.artifacts.values():
                if artifact.job_id == job_id and artifact.deleted_at is None:
                    artifact.expires_at = expires_at

    def recover_interrupted_jobs(self, interrupted_at: datetime) -> int:
        recovered = 0
        with self._lock:
            for job in self.jobs.values():
                if job.status not in ACTIVE_JOB_STATUSES or job.created_at >= interrupted_at:
                    continue
                job.failed_stage = job.current_stage or job.status.value
                job.status = JobStatus.FAILED
                job.error = (
                    "Processing was interrupted when the backend restarted. "
                    "Your saved work is safe—retry this job to continue."
                )
                job.progress_message = "Interrupted by backend restart"
                job.retryable = True
                job.heartbeat_at = interrupted_at
                recovered += 1
        return recovered


class SupabaseJobRepository:
    def __init__(self, url: str, service_role_key: str):
        try:
            from supabase import create_client
        except ImportError as exc:
            raise RuntimeError("Install dependencies from requirements.txt to use Supabase") from exc
        self._url = url
        self._service_role_key = service_role_key
        self._create_client = create_client
        self._client = None
        self._client_lock = threading.RLock()

    @property
    def client(self):
        """Lazily construct the shared client.

        FastAPI polling and background processing run concurrently. Sharing one
        HTTP/2 connection is safe only when repository operations serialize
        access to it through ``_execute`` below.
        """
        with self._client_lock:
            if self._client is None:
                self._client = self._create_client(self._url, self._service_role_key)
            return self._client

    def _reset_client(self) -> None:
        if self._client is not None:
            try:
                self._client.postgrest.session.close()
            except Exception:
                pass
        self._client = None

    def _execute(self, operation, description: str):
        """Retry transient transport failures with a fresh connection."""
        import httpx

        with self._client_lock:
            for attempt in range(3):
                try:
                    return operation(self.client)
                except httpx.TransportError as exc:
                    self._reset_client()
                    if attempt == 2:
                        logger.error("Supabase %s failed after retries: %s", description, exc)
                        raise
                    delay = 0.15 * (2**attempt)
                    logger.warning(
                        "Transient Supabase transport error during %s; retrying in %.2fs",
                        description,
                        delay,
                    )
                    time.sleep(delay)

    @staticmethod
    def _job_row(
        job: Job,
        include_quality_preferences: bool = True,
        include_reliability: bool = True,
        include_content_mode: bool = True,
    ) -> dict:
        row = {
            "id": job.id,
            "user_id": job.user_id,
            "status": job.status.value,
            "source_type": job.source_type,
            "source_value": job.source_value,
            "error": job.error,
            "video_path": job.video_path,
            "transcript": job.transcript,
            "creative_prompt": job.creative_prompt,
            "output_format": job.output_format.value,
            "caption_style": job.caption_style.value,
        }
        if include_content_mode:
            row["content_mode"] = job.content_mode.value
        if include_quality_preferences:
            row.update(
                {
                    "target_clip_count": job.target_clip_count,
                    "min_clip_duration": job.min_clip_duration,
                    "max_clip_duration": job.max_clip_duration,
                }
            )
        if include_reliability:
            row.update(
                {
                    "progress_percent": job.progress_percent,
                    "progress_message": job.progress_message,
                    "current_stage": job.current_stage,
                    "processed_clips": job.processed_clips,
                    "total_clips": job.total_clips,
                    "heartbeat_at": (
                        job.heartbeat_at.isoformat() if job.heartbeat_at else None
                    ),
                    "failed_stage": job.failed_stage,
                    "retryable": job.retryable,
                    "cancel_requested_at": (
                        job.cancel_requested_at.isoformat()
                        if job.cancel_requested_at
                        else None
                    ),
                }
            )
        return row

    def _save_job(self, job: Job, description: str) -> None:
        """Persist preferences, while keeping the app usable before migration 003."""
        from postgrest.exceptions import APIError

        try:
            self._execute(
                lambda client: client.table("jobs")
                .upsert(self._job_row(job))
                .execute(),
                description,
            )
        except APIError as exc:
            if "content_mode" in str(exc):
                logger.warning(
                    "Supabase migration 006 is not applied yet; Gaming/OBS mode "
                    "will only live in this backend process."
                )
                self._execute(
                    lambda client: client.table("jobs")
                    .upsert(self._job_row(job, include_content_mode=False))
                    .execute(),
                    f"{description} without Gaming/OBS mode",
                )
                return
            reliability_columns = (
                "progress_percent",
                "progress_message",
                "current_stage",
                "processed_clips",
                "total_clips",
                "heartbeat_at",
                "failed_stage",
                "retryable",
                "cancel_requested_at",
            )
            if any(column in str(exc) for column in reliability_columns):
                logger.warning(
                    "Supabase migration 005 is not applied yet; reliability state "
                    "will only live in this backend process."
                )
                self._execute(
                    lambda client: client.table("jobs")
                    .upsert(self._job_row(job, include_reliability=False))
                    .execute(),
                    f"{description} without reliability state",
                )
                return
            missing_preferences = any(
                column in str(exc)
                for column in (
                    "target_clip_count",
                    "min_clip_duration",
                    "max_clip_duration",
                )
            )
            if not missing_preferences:
                raise
            logger.warning(
                "Supabase migration 003 is not applied yet; quality preferences "
                "will work for this running job but will not be persisted."
            )
            self._execute(
                lambda client: client.table("jobs")
                .upsert(self._job_row(job, include_quality_preferences=False))
                .execute(),
                f"{description} without quality preferences",
            )

    @staticmethod
    def _clip_row(
        job_id: str,
        clip: Clip,
        include_score_breakdown: bool = True,
        include_layout_mode: bool = True,
    ) -> dict:
        row = {
            "id": clip.id,
            "job_id": job_id,
            "title": clip.title,
            "start_seconds": clip.start,
            "end_seconds": clip.end,
            "score": clip.score,
            "reason": clip.reason,
            "file_path": clip.file_path,
            "output_format": clip.output_format.value,
            "caption_style": clip.caption_style.value,
        }
        if include_score_breakdown:
            row["score_breakdown"] = clip.score_breakdown
        if include_layout_mode:
            row["layout_mode"] = clip.layout_mode.value
        return row

    def create_job(
        self,
        source_type: str,
        source_value: str,
        user_id: str | None,
        creative_prompt: str | None,
        output_format: OutputFormat,
        caption_style: CaptionStyle,
        target_clip_count: int,
        min_clip_duration: int,
        max_clip_duration: int,
        content_mode: ContentMode = ContentMode.GENERAL,
    ) -> Job:
        job = Job(
            source_type=source_type,
            source_value=source_value,
            user_id=user_id,
            creative_prompt=creative_prompt,
            output_format=output_format,
            caption_style=caption_style,
            target_clip_count=target_clip_count,
            min_clip_duration=min_clip_duration,
            max_clip_duration=max_clip_duration,
            content_mode=content_mode,
        )
        self._save_job(job, "job creation")
        return job

    def get_job(self, job_id: str) -> Job | None:
        response = self._execute(
            lambda client: client.table("jobs")
            .select("*")
            .eq("id", job_id)
            .limit(1)
            .execute(),
            "job lookup",
        )
        if not response.data:
            return None
        row = response.data[0]
        clips_response = self._execute(
            lambda client: client.table("clips")
            .select("*")
            .eq("job_id", job_id)
            .order("start_seconds")
            .execute(),
            "clip lookup",
        )
        clips = [
            Clip(
                id=clip["id"],
                title=clip["title"],
                start=clip["start_seconds"],
                end=clip["end_seconds"],
                score=clip["score"],
                score_breakdown=clip.get("score_breakdown") or {},
                reason=clip.get("reason") or "",
                file_path=clip.get("file_path"),
                output_format=clip.get("output_format") or OutputFormat.VERTICAL,
                caption_style=clip.get("caption_style") or CaptionStyle.BOLD,
                layout_mode=clip.get("layout_mode") or LayoutMode.SMART,
            )
            for clip in clips_response.data
        ]
        return Job(
            id=row["id"],
            user_id=row.get("user_id"),
            status=row["status"],
            source_type=row["source_type"],
            source_value=row["source_value"],
            error=row.get("error"),
            video_path=row.get("video_path"),
            transcript=row.get("transcript"),
            clips=clips,
            creative_prompt=row.get("creative_prompt"),
            output_format=row.get("output_format") or OutputFormat.VERTICAL,
            caption_style=row.get("caption_style") or CaptionStyle.BOLD,
            content_mode=row.get("content_mode") or ContentMode.GENERAL,
            target_clip_count=row.get("target_clip_count") or 5,
            min_clip_duration=row.get("min_clip_duration") or 20,
            max_clip_duration=row.get("max_clip_duration") or 60,
            progress_percent=row.get("progress_percent") or 0,
            progress_message=row.get("progress_message") or "Waiting to start…",
            current_stage=row.get("current_stage") or row.get("status") or "queued",
            processed_clips=row.get("processed_clips") or 0,
            total_clips=row.get("total_clips") or 0,
            heartbeat_at=row.get("heartbeat_at"),
            failed_stage=row.get("failed_stage"),
            retryable=bool(row.get("retryable", False)),
            cancel_requested_at=row.get("cancel_requested_at"),
            created_at=row.get("created_at") or datetime.now(timezone.utc),
        )

    def update_job(self, job: Job) -> None:
        self._save_job(job, "job update")
        if job.clips:
            from postgrest.exceptions import APIError

            rows = [self._clip_row(job.id, clip) for clip in job.clips]
            try:
                self._execute(
                    lambda client: client.table("clips").upsert(rows).execute(),
                    "clip update",
                )
            except APIError as exc:
                if "layout_mode" in str(exc):
                    logger.warning(
                        "Supabase migration 006 is not applied yet; clip layout mode "
                        "will not be persisted."
                    )
                    fallback_rows = [
                        self._clip_row(job.id, clip, include_layout_mode=False)
                        for clip in job.clips
                    ]
                    self._execute(
                        lambda client: client.table("clips").upsert(fallback_rows).execute(),
                        "clip update without Gaming/OBS layout",
                    )
                    return
                if "score_breakdown" not in str(exc):
                    raise
                logger.warning(
                    "Supabase migration 004 is not applied yet; score breakdowns "
                    "will not be persisted."
                )
                fallback_rows = [
                    self._clip_row(job.id, clip, include_score_breakdown=False)
                    for clip in job.clips
                ]
                self._execute(
                    lambda client: client.table("clips").upsert(fallback_rows).execute(),
                    "clip update without score breakdowns",
                )

    def register_artifact(self, artifact: Artifact) -> Artifact:
        row = artifact.model_dump(mode="json")
        row["kind"] = artifact.kind.value
        self._execute(
            lambda client: client.table("artifacts").upsert(row).execute(),
            "artifact registration",
        )
        return artifact

    def find_artifact(
        self, job_id: str, kind: ArtifactKind, clip_id: str | None = None
    ) -> Artifact | None:
        def lookup(client):
            query = (
                client.table("artifacts")
                .select("*")
                .eq("job_id", job_id)
                .eq("kind", kind.value)
                .is_("deleted_at", "null")
            )
            query = query.eq("clip_id", clip_id) if clip_id else query.is_("clip_id", "null")
            return query.limit(1).execute()

        response = self._execute(lookup, "artifact lookup")
        return Artifact.model_validate(response.data[0]) if response.data else None

    def list_expired_artifacts(self, now: datetime) -> list[Artifact]:
        response = self._execute(
            lambda client: client.table("artifacts")
            .select("*")
            .is_("deleted_at", "null")
            .lte("expires_at", now.isoformat())
            .limit(1000)
            .execute(),
            "expired artifact lookup",
        )
        return [Artifact.model_validate(row) for row in response.data]

    def mark_artifact_deleted(self, artifact_id: str, deleted_at: datetime) -> None:
        self._execute(
            lambda client: client.table("artifacts")
            .update({"deleted_at": deleted_at.isoformat()})
            .eq("id", artifact_id)
            .execute(),
            "artifact deletion update",
        )

    def set_job_artifact_expiry(self, job_id: str, expires_at: datetime) -> None:
        self._execute(
            lambda client: client.table("artifacts")
            .update({"expires_at": expires_at.isoformat()})
            .eq("job_id", job_id)
            .is_("deleted_at", "null")
            .execute(),
            "job artifact expiry update",
        )

    def recover_interrupted_jobs(self, interrupted_at: datetime) -> int:
        from postgrest.exceptions import APIError

        active_values = [status.value for status in ACTIVE_JOB_STATUSES]
        try:
            response = self._execute(
                lambda client: client.table("jobs")
                .select("id,status,current_stage")
                .in_("status", active_values)
                .lt("created_at", interrupted_at.isoformat())
                .execute(),
                "interrupted job lookup",
            )
            for row in response.data or []:
                failed_stage = row.get("current_stage") or row.get("status")
                self._execute(
                    lambda client, job_id=row["id"], stage=failed_stage: client.table("jobs")
                    .update(
                        {
                            "status": JobStatus.FAILED.value,
                            "error": (
                                "Processing was interrupted when the backend restarted. "
                                "Your saved work is safe—retry this job to continue."
                            ),
                            "progress_message": "Interrupted by backend restart",
                            "failed_stage": stage,
                            "retryable": True,
                            "heartbeat_at": interrupted_at.isoformat(),
                        }
                    )
                    .eq("id", job_id)
                    .execute(),
                    "interrupted job update",
                )
        except APIError as exc:
            if any(
                column in str(exc)
                for column in (
                    "current_stage",
                    "progress_message",
                    "failed_stage",
                    "retryable",
                    "heartbeat_at",
                )
            ):
                logger.warning(
                    "Skipping interrupted-job recovery until Supabase migration 005 is applied."
                )
                return 0
            raise
        return len(response.data or [])


def _build_repository() -> JobRepository:
    if SUPABASE_CONFIGURED:
        logger.info("Using Supabase for job and artifact persistence")
        return SupabaseJobRepository(SUPABASE_URL, SUPABASE_SERVER_KEY)
    logger.warning(
        "Supabase is not configured; using temporary in-memory job metadata. "
        "Add SUPABASE_URL and SUPABASE_SECRET_KEY to .env for persistence."
    )
    return MemoryJobRepository()


repository: JobRepository = _build_repository()


def create_job(
    source_type: str,
    source_value: str,
    user_id: str | None = None,
    creative_prompt: str | None = None,
    output_format: OutputFormat = OutputFormat.VERTICAL,
    caption_style: CaptionStyle = CaptionStyle.BOLD,
    target_clip_count: int = 5,
    min_clip_duration: int = 20,
    max_clip_duration: int = 60,
    content_mode: ContentMode = ContentMode.GENERAL,
) -> Job:
    return repository.create_job(
        source_type,
        source_value,
        user_id,
        creative_prompt,
        output_format,
        caption_style,
        target_clip_count,
        min_clip_duration,
        max_clip_duration,
        content_mode,
    )


def get_job(job_id: str) -> Job | None:
    return repository.get_job(job_id)


def update_job(job: Job) -> None:
    repository.update_job(job)


def register_artifact(
    *,
    job_id: str,
    kind: ArtifactKind,
    local_path: str,
    ttl: timedelta,
    size_bytes: int = 0,
    clip_id: str | None = None,
) -> Artifact:
    artifact = Artifact(
        job_id=job_id,
        clip_id=clip_id,
        kind=kind,
        local_path=local_path,
        size_bytes=size_bytes,
        expires_at=datetime.now(timezone.utc) + ttl,
    )
    return repository.register_artifact(artifact)


def find_artifact(job_id: str, kind: ArtifactKind, clip_id: str | None = None) -> Artifact | None:
    return repository.find_artifact(job_id, kind, clip_id)


def list_expired_artifacts(now: datetime | None = None) -> list[Artifact]:
    return repository.list_expired_artifacts(now or datetime.now(timezone.utc))


def mark_artifact_deleted(artifact_id: str) -> None:
    repository.mark_artifact_deleted(artifact_id, datetime.now(timezone.utc))


def set_job_artifact_expiry(job_id: str, ttl: timedelta) -> None:
    repository.set_job_artifact_expiry(job_id, datetime.now(timezone.utc) + ttl)


def recover_interrupted_jobs(interrupted_before: datetime | None = None) -> int:
    return repository.recover_interrupted_jobs(
        interrupted_before or datetime.now(timezone.utc)
    )
