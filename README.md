# Clipmaker

Clipmaker turns a long YouTube video or uploaded video into short, captioned
clips. The current Phase 1 architecture uses Supabase for metadata and a safe,
job-scoped local filesystem for media.

## Phase 1 stack

- FastAPI and Uvicorn
- Groq Whisper transcription
- Groq structured-output highlight selection
- FFmpeg, libx264, and libass
- Supabase PostgreSQL for jobs, clips, and artifact metadata
- Optional Supabase access-token validation
- Local media storage with automatic retention cleanup
- Plain HTML, CSS, and JavaScript frontend

## Creative controls and clip studio

- Optional viewer brief guides highlight selection without being required.
- Generate `16:9`, `9:16`, or source-aspect clips from the same video.
- Bold, clean, and karaoke caption presets scale to each output canvas.
- The editor uses a clean source-aspect preview for accurate 16:9/9:16
  switching. Manual reframing supports draggable horizontal/vertical focus,
  zoom, and timeline keyframes whose interpolation matches FFmpeg export.
- Trim controls report whether the selected IN/OUT points are speech-safe and
  can snap manual edits to complete spoken phrases.
- Smart Reframe uses the local OpenCV YuNet model, persistent multi-face
  tracks, mouth-motion evidence, scene-cut recovery, natural headroom, and a
  speed-limited FFmpeg crop path. The Studio previews this planned path and
  reports its confidence; Manual remains a precise override.
- Users can request 3, 5, or 8 clips and choose quick, balanced, or deep
  duration ranges.
- Highlight candidates are ranked for hook, prompt relevance, completeness,
  and shareability, then snapped to natural sentence or pause boundaries.
- Candidates without a clean spoken ending are rejected instead of being cut
  mid-thought; a short duration grace may be used to finish a sentence.
- Quality scores are calibrated so one weak dimension caps the overall score;
  semantically repetitive candidates are removed even when timestamps differ.
- Processing exposes persisted percentage, stage, heartbeat, and clip counts.
- Failed, cancelled, or restart-interrupted jobs can retry from saved source,
  transcript, and completed clips where those artifacts still exist.

## Storage lifecycle

Every job is isolated under `clipmaker-backend/storage/jobs/{job_id}`:

```text
source/       uploaded or downloaded original
temp/         audio and subtitle processing files
outputs/      generated and edited clips
thumbnails/   editor timeline frames
```

- MP3, ASS, and other temporary files are deleted immediately.
- Failed-job artifacts expire after 1 hour.
- Thumbnails expire after 24 hours.
- Source videos expire after 24 hours.
- Final clips expire after 7 days.
- Cleanup runs at startup and every 30 minutes.

All durations are configurable in `.env`.

## Setup

Backend:

```bash
cd clipmaker-backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Follow [clipmaker-backend/SUPABASE_SETUP.md](clipmaker-backend/SUPABASE_SETUP.md),
then add the Groq and Supabase values to `.env`.

Run the API:

```bash
uvicorn main:app --reload --port 8000
```

Run the frontend in another terminal:

```bash
cd clipmaker-frontend
python3 -m http.server 5500
```

Open <http://localhost:5500>. API documentation is available at
<http://localhost:8000/docs>.

## Tests

```bash
cd clipmaker-backend
.venv/bin/python -m unittest discover -s tests -v
```

## Quality evaluation

The automated test suite protects code behavior. Product quality is evaluated
separately on a private, repeatable 15-scenario video suite covering single and
multiple speakers, difficult detection, scene changes, B-roll, composition,
captions, and speech boundaries.

```bash
cd clipmaker-backend
.venv/bin/python quality_eval.py init
.venv/bin/python quality_eval.py run --render
```

The evaluator produces local JSON and HTML reports, supports 1–5 human review
ratings, and compares candidate results with a known-good baseline. See
[clipmaker-backend/evaluation/README.md](clipmaker-backend/evaluation/README.md).

## Safe cleanup

```bash
cd clipmaker-backend
.venv/bin/python cleanup_storage.py --dry-run
.venv/bin/python cleanup_storage.py --execute
```

Run dry-run first whenever retention settings change.
