# Clipmaker API

FastAPI backend for downloading/uploading videos, transcribing speech, finding
highlight segments, cutting clips, and burning word-timed captions.

## Media versus metadata

- Supabase PostgreSQL stores jobs, clips, artifact metadata, expiry times, and
  optional user ownership.
- Video and processing files remain under `storage/jobs/{job_id}`.
- The service never stores video bytes inside PostgreSQL.
- When Supabase is not configured, startup logs a warning and uses an in-memory
  metadata fallback for setup and tests.

Apply the SQL files in `supabase/migrations/` in numeric order before enabling
Supabase. Migration 004 persists each clip's quality-score breakdown and
migration 005 adds progress, cancellation, retry, and restart recovery.
See [SUPABASE_SETUP.md](SUPABASE_SETUP.md).

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --port 8000
```

FFmpeg must include `libx264`, `libmp3lame`, and the `ass`/libass filter.

## API flow

1. `POST /api/jobs` with a YouTube URL or `POST /api/jobs/upload`.
2. Poll `GET /api/jobs/{job_id}`.
3. Preview, download, request thumbnails, or render a bounded edit.
4. Expired media returns HTTP 410 while metadata remains available.

Active jobs support `POST /api/jobs/{job_id}/cancel`. Failed or cancelled jobs
that still have usable input support `POST /api/jobs/{job_id}/retry`.

Studio previews are always cut from the uncropped source segment. Manual
renders accept `crop_x`, `crop_y`, `crop_zoom`, and ordered `crop_keyframes` so
the browser canvas and FFmpeg output share the same pan/zoom geometry.

Vertical Auto renders use the bundled local YuNet detector. Smart Reframe
associates multiple faces across frames, uses mouth motion as one signal for
the likely speaker, resets tracking on scene cuts, selects conservative zoom,
adds natural headroom, and limits crop speed. The editor requests
`GET /api/jobs/{job_id}/clips/{clip_id}/reframe-analysis` to preview the exact
cached path that export will reuse. No video frames leave the machine.

The API accepts Supabase Bearer tokens. Keep `AUTH_REQUIRED=false` until the
frontend sign-in flow is connected, then change it to `true`.

## Product-quality gate

Run `quality_eval.py` against the private 15-scenario suite before changing the
tracking or rendering algorithm. It measures detection coverage, confidence,
crop speed, edge holds, subject switches, centered fallback, and real 9:16
render integrity. Optional human ratings cover active-speaker correctness,
composition, stability, speech completeness, and caption readability.

```bash
.venv/bin/python quality_eval.py init
.venv/bin/python quality_eval.py run --render
```

See [evaluation/README.md](evaluation/README.md) for baseline comparisons and
the local-video setup. Evaluation videos, ratings, renders, and reports are
ignored by Git.

## Safety controls

- Server-generated upload paths; client filenames are never trusted.
- 500 MB upload limit by default.
- 15-minute duration limit for both URL and uploaded videos.
- YouTube-only URL validation.
- Validated, non-overlapping model-generated highlights.
- Per-job ownership checks when an authenticated user created the job.
- Temporary-file cleanup in `finally` blocks.
- Scheduled expiry cleanup with a dry-run command.
