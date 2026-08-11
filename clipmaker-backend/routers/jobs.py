import shutil
import uuid
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from config import UPLOADS_DIR
from services.job_store import create_job, get_job
from services.pipeline import run_pipeline
from services import video_cutter, caption_burner
from services.exceptions import VideoDownloadError

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class CreateJobFromUrl(BaseModel):
    url: str


class RenderClipRequest(BaseModel):
    start: float  # seconds, relative to the ORIGINAL clip's own start (0 = clip start)
    end: float  # seconds, relative to the original clip's own start
    captions: bool = True


def _serialize_clip(job_id: str, clip: dict) -> dict:
    """Shapes a clip for the API response - never leaks server file paths,
    only URLs the frontend can actually use."""
    out = {k: v for k, v in clip.items() if k != "file_path"}
    out["preview_url"] = f"/media/{Path(clip['file_path']).name}" if clip.get("file_path") else None
    out["download_url"] = f"/api/jobs/{job_id}/clips/{clip['id']}/download"
    out["thumbnails_url"] = f"/api/jobs/{job_id}/clips/{clip['id']}/thumbnails"
    out["render_url"] = f"/api/jobs/{job_id}/clips/{clip['id']}/render"
    return out


@router.post("")
def create_job_from_url(payload: CreateJobFromUrl, background_tasks: BackgroundTasks):
    job = create_job(source_type="url", source_value=payload.url)
    background_tasks.add_task(run_pipeline, job)
    return {"job_id": job.id, "status": job.status}


@router.post("/upload")
def create_job_from_upload(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    dest = UPLOADS_DIR / f"{file.filename}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    job = create_job(source_type="upload", source_value=str(dest))
    background_tasks.add_task(run_pipeline, job)
    return {"job_id": job.id, "status": job.status}


@router.get("/{job_id}")
def get_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job.id,
        "status": job.status,
        "error": job.error,
        "clips": [_serialize_clip(job.id, c) for c in job.clips],
    }


@router.get("/{job_id}/clips/{clip_id}/download")
def download_clip(job_id: str, clip_id: str):
    """Forces a real file download (Content-Disposition: attachment) -
    use this for the 'Download' button. For inline <video> preview in
    the browser, use the clip's preview_url (served via /media) instead,
    which does NOT force a download."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    clip = next((c for c in job.clips if c["id"] == clip_id), None)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    path = Path(clip["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Clip file missing on disk")
    safe_title = "".join(ch for ch in clip["title"] if ch.isalnum() or ch in " -_").strip() or "clip"
    return FileResponse(path, media_type="video/mp4", filename=f"{safe_title}.mp4")


@router.get("/{job_id}/clips/{clip_id}/thumbnails")
def get_clip_thumbnails(job_id: str, clip_id: str):
    """Extracts (or reuses already-extracted) frame thumbnails for this
    clip's raw video, for the trim editor's timeline strip."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    clip = next((c for c in job.clips if c["id"] == clip_id), None)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    raw_path = Path(clip["file_path"]).parent / f"{clip_id}_raw.mp4"
    if not raw_path.exists():
        raise HTTPException(status_code=404, detail="Source clip file missing on disk")

    try:
        thumb_paths = video_cutter.extract_thumbnails(raw_path, clip_id, interval_seconds=1.0)
    except VideoDownloadError as e:
        raise HTTPException(status_code=500, detail=e.user_message)

    urls = [f"/media/{Path(p).parent.name}/{Path(p).name}" for p in thumb_paths]
    return {"thumbnails": urls, "clip_duration": clip["end"] - clip["start"]}


@router.post("/{job_id}/clips/{clip_id}/render")
def render_clip(job_id: str, clip_id: str, payload: RenderClipRequest):
    """Re-cuts this clip using new trim points (relative to the clip's own
    timeline, so 0 = original clip start) and optionally burns captions.
    Returns a new preview/download URL - the original clip is untouched,
    so re-editing is non-destructive."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    clip = next((c for c in job.clips if c["id"] == clip_id), None)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if not job.video_path:
        raise HTTPException(status_code=400, detail="Original source video is no longer available")

    if payload.end <= payload.start:
        raise HTTPException(status_code=400, detail="End time must be after start time")

    # translate the trim (relative to the clip) back into absolute
    # timestamps on the original source video
    absolute_start = clip["start"] + payload.start
    absolute_end = clip["start"] + payload.end

    render_id = f"{clip_id}_edit_{uuid.uuid4().hex[:8]}"
    source_path = Path(job.video_path)

    try:
        raw_render_path = video_cutter.cut_clip(source_path, render_id, absolute_start, absolute_end)

        if payload.captions and job.transcript:
            clip_words = [
                w for w in job.transcript
                if absolute_start <= w["start"] <= absolute_end
            ]
            ass_path = caption_burner.build_ass_file(clip_words, render_id, clip_start_offset=absolute_start)
            final_path = caption_burner.burn_captions(raw_render_path, ass_path, render_id)
        else:
            final_path = raw_render_path
    except VideoDownloadError as e:
        raise HTTPException(status_code=500, detail=e.user_message)

    return {
        "preview_url": f"/media/{final_path.name}",
        "download_url": f"/media/{final_path.name}",  # direct static link is fine for re-rendered edits
    }
