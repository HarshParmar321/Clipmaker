"""
Orchestrates the full clip-generation pipeline for one job:
download -> transcribe -> detect highlights -> cut -> caption -> done.

Runs as a FastAPI BackgroundTask. For production, swap this for a proper
task queue (Celery + Redis) so jobs survive server restarts and you get
retry/backoff for free - but this is fine for a demo/MVP.
"""
import logging
import uuid
from pathlib import Path
from services.job_store import Job, JobStatus, update_job
from services.exceptions import VideoDownloadError
from services import downloader, transcriber, highlight_detector, video_cutter, caption_burner

logger = logging.getLogger("clipmaker.pipeline")


def run_pipeline(job: Job) -> None:
    try:
        # 1. Get the source video onto disk
        if job.source_type == "url":
            job.status = JobStatus.DOWNLOADING
            update_job(job)
            video_path = downloader.download_video(job.source_value, job.id)
        else:
            video_path = Path(job.source_value)  # already-uploaded file path

        job.video_path = str(video_path)

        # 2. Transcribe with word-level timestamps
        job.status = JobStatus.TRANSCRIBING
        update_job(job)
        words = transcriber.transcribe(video_path)
        job.transcript = words

        # 3. Ask the LLM which segments are clip-worthy
        job.status = JobStatus.DETECTING_HIGHLIGHTS
        update_job(job)
        raw_clips = highlight_detector.detect_highlights(words)
        logger.info(f"[{job.id}] detected {len(raw_clips)} highlight(s):")
        for rc in raw_clips:
            logger.info(f"[{job.id}]   '{rc.get('title')}' {rc.get('start')}s-{rc.get('end')}s score={rc.get('score')}")

        # 4. Cut + caption each clip
        job.status = JobStatus.CUTTING_CLIPS
        update_job(job)

        for rc in raw_clips:
            clip_id = str(uuid.uuid4())
            start, end = float(rc["start"]), float(rc["end"])

            job.status = JobStatus.BURNING_CAPTIONS
            update_job(job)

            # Build the .ass subtitle file first (instant, no ffmpeg)
            clip_words = [w for w in words if start <= w["start"] <= end]
            ass_path = caption_burner.build_ass_file(clip_words, clip_id, clip_start_offset=start)

            # Single-pass: cut + burn captions in one ffmpeg call (~2x faster)
            final_path = video_cutter.cut_and_caption(video_path, clip_id, start, end, ass_path)

            # Also generate the raw (no-caption) clip for the trim editor
            video_cutter.cut_clip(video_path, clip_id, start, end)

            job.clips.append({
                "id": clip_id,
                "title": rc.get("title", "Untitled clip"),
                "start": start,
                "end": end,
                "score": rc.get("score", 0),
                "reason": rc.get("reason", ""),
                "file_path": str(final_path),
            })
            update_job(job)

        job.status = JobStatus.DONE
        update_job(job)

    except VideoDownloadError as e:
        # clean, user-facing message - but the real technical detail still
        # goes to server logs so you (the developer) can actually debug
        # what happened, instead of it vanishing along with the exception.
        logger.error(f"[{job.id}] {e.user_message} | detail: {e.technical_detail}")
        job.status = JobStatus.FAILED
        job.error = e.user_message
        update_job(job)

    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = str(e)
        update_job(job)
