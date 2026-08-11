"""
Very simple in-memory job store.

Good enough for local dev / demo. Swap this for Redis (or a Postgres/Supabase
table) once you deploy for real users, since this dict resets on server
restart and won't work across multiple server instances.
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class JobStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    DETECTING_HIGHLIGHTS = "detecting_highlights"
    CUTTING_CLIPS = "cutting_clips"
    BURNING_CAPTIONS = "burning_captions"
    DONE = "done"
    FAILED = "failed"


class Clip(BaseModel):
    id: str
    title: str
    start: float  # seconds
    end: float  # seconds
    score: int  # 0-100, how "clip-worthy" the LLM thinks this is
    reason: str = ""
    file_path: Optional[str] = None  # set once the captioned clip is rendered


class Job(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: JobStatus = JobStatus.QUEUED
    source_type: str  # "url" or "upload"
    source_value: str  # the URL, or the path to the uploaded file
    error: Optional[str] = None
    video_path: Optional[str] = None
    transcript: Optional[list] = None  # list of word-level segments
    clips: list[Clip] = Field(default_factory=list)


# job_id -> Job
_jobs: dict[str, Job] = {}


def create_job(source_type: str, source_value: str) -> Job:
    job = Job(source_type=source_type, source_value=source_value)
    _jobs[job.id] = job
    return job


def get_job(job_id: str) -> Optional[Job]:
    return _jobs.get(job_id)


def update_job(job: Job) -> None:
    _jobs[job.id] = job
