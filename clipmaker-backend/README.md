# Clipmaker API

Backend that takes a video (link or upload), transcribes it, uses an LLM to
pick the best short-form clip segments, cuts them, and burns in animated
word-by-word captions.

## Setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

You also need **ffmpeg** installed on your system (not a pip package):
- Mac: `brew install ffmpeg`
- Ubuntu/Debian: `sudo apt install ffmpeg`
- Windows: download from ffmpeg.org and add to PATH

Copy `.env.example` to `.env` and add your Groq API key (free at
console.groq.com/keys):

```bash
cp .env.example .env
```

## Run

```bash
uvicorn main:app --reload --port 8000
```

Visit `http://localhost:8000/docs` for interactive API docs (FastAPI's
built-in Swagger UI) - use this to test endpoints before wiring up the
frontend.

## API flow

1. `POST /api/jobs` with `{"url": "https://youtube.com/..."}` -> returns `job_id`
   OR `POST /api/jobs/upload` with a multipart file -> returns `job_id`
2. Poll `GET /api/jobs/{job_id}` until `status` is `"done"` (or `"failed"`)
3. Each clip in the response has a `download_url` - fetch that to get the
   final captioned .mp4

## Project structure

```
main.py                    FastAPI app entrypoint
config.py                  env vars + storage paths
routers/jobs.py            HTTP endpoints
services/job_store.py      in-memory job tracking (swap for Redis/DB later)
services/pipeline.py       orchestrates the full pipeline per job
services/downloader.py     yt-dlp video download
services/transcriber.py    Groq Whisper transcription (word-level timestamps)
services/highlight_detector.py   Groq LLaMA picks the best clip segments
services/video_cutter.py   ffmpeg clip cutting + thumbnail extraction
services/caption_burner.py .ass caption generation + ffmpeg burn-in
```

## Known limitations (v1 / demo scope)

- Videos capped at 15 minutes (see `MAX_DURATION_SECONDS` in `downloader.py`)
  to keep processing time reasonable on free-tier hosting.
- YouTube links only for now - other platforms need auth cookies that break
  often when the platform changes its API.
- Jobs are stored in memory - restarting the server loses job history.
- One caption style for now (see `STYLE` dict in `caption_burner.py`).

## Next steps

- Build the React frontend against this API (upload/link input, polling,
  timeline with thumbnails, clip list).
- Add `GET /api/jobs/{job_id}/thumbnails` if you want frame thumbnails for
  the source video timeline (the extraction function already exists in
  `video_cutter.py`, just needs a route + static file serving).
- Swap the in-memory job store for Redis or a Supabase table once this
  needs to handle more than one user at a time.
