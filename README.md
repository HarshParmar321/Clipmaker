<h1 align="center">Clipmaker</h1>

<p align="center">
  <strong>Long video in. Scroll-stopping clips out.</strong>
</p>

<p align="center">
  Paste a link or drop a file. Clipmaker transcribes it, finds the moments worth
  clipping, cuts them to vertical, tracks the speaker's face, and burns in
  captions — then hands you a studio to fix anything it got wrong.
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white">
  <img alt="FFmpeg" src="https://img.shields.io/badge/FFmpeg-007808?style=flat-square&logo=ffmpeg&logoColor=white">
  <img alt="Supabase" src="https://img.shields.io/badge/Supabase-3FCF8E?style=flat-square&logo=supabase&logoColor=white">
  <img alt="Tests" src="https://img.shields.io/badge/tests-214%20passing-2E7D5B?style=flat-square">
</p>

---

## What it does

Give it a three-hour podcast or a four-hour Twitch VOD. It gives you back five
vertical clips that are actually worth posting.

```
  URL or upload
        ↓
  download      best available source, up to 1440p
        ↓
  transcribe    Whisper, word-level timing, any length
        ↓
  detect        rank hook · relevance · completeness · shareability
        ↓
  cut           9:16 reframe that follows the speaker
        ↓
  caption       burned in, word-by-word, in the spoken language
        ↓
  studio        trim, reframe, retime, retype, re-render
```

Nothing is a black box. Every clip arrives with its score breakdown and a reason,
and every decision the pipeline made is editable before you export.

---

## Features

### Finding the moment

Highlights are ranked on four axes — **hook**, **relevance**, **completeness** and
**shareability** — then snapped to real sentence or pause boundaries so clips
never open or close mid-thought. Selection is tiered: strong distinct moments win
first, and weaker ones only fill slots that would otherwise be left empty. Ask for
five, get five.

A **creative brief** is optional. Type *"find practical advice for first-time
founders"* and it steers the ranking without constraining it.

### Making it look right

**Smart Reframe** uses a local OpenCV YuNet model with persistent multi-face
tracks, mouth-motion evidence, scene-cut recovery and natural headroom, then
plans a speed-limited crop path so the frame glides rather than snapping. The
studio previews the exact path the export will take and reports its confidence.
Zoom is bounded by what the source can actually support, so reframing never
invents pixels.

**Gaming mode** detects a small, stable corner facecam and builds a true
Gameplay + Reaction portrait composition — 65% gameplay, 35% enlarged reaction, a
protected caption boundary, and manual corner correction when detection guesses
wrong.

### Captions that read

Bold, Clean and Karaoke presets, all scaling proportionally to the output canvas.
Word-level timing drives the highlight effect. Captions render in the **language
that was spoken**, with an optional English translation that preserves phrase
timing — and the fonts for every script are bundled, so Hindi renders the same on
a laptop and a Linux server.

### The studio

Trim with speech-safe boundaries. Drag the crop. Set keyframes whose
interpolation matches the FFmpeg export exactly. Edit the transcript and the
captions follow. Undo everything. The preview is the export.

---

## Supported sources

**YouTube** · **Twitch** · **Vimeo** · **Loom** · **X (Twitter)** · **Reddit** ·
**Instagram** · **TikTok** · **Facebook** · **Dailymotion** — or upload any video
file directly.

> **Direct upload is the reliable path.** URL ingest depends on ten platforms that
> can change their defences without notice, and an uploaded master is higher
> quality than anything a platform hands back. Clipmaker degrades gracefully and
> tells you when a source is poor, but upload is the path that always works.

| Limit | Value |
|---|---|
| Maximum video length | 4 hours |
| Maximum upload size | 500 MB (configurable) |
| Clips per job | 3, 5 or 8 |
| Clip duration | quick · balanced · deep (20–90s) |
| Output formats | 9:16, 16:9, source aspect |

---

## Getting started

### Prerequisites

| | Why |
|---|---|
| **Python 3.11+** | backend |
| **FFmpeg with libass** | cutting and caption burning — checked at startup |
| **Node.js or Deno** | yt-dlp needs a JS runtime for YouTube's high-resolution formats |
| **Groq API key** | transcription and highlight ranking — [free tier](https://console.groq.com/keys) |
| **Supabase project** | job metadata (optional — falls back to memory) |

```bash
# macOS
brew install ffmpeg node
```

### Backend

```bash
cd clipmaker-backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env     # add your GROQ_API_KEY
python -m uvicorn main:app --reload --port 8000
```

Watch the startup checks. They are there to catch the things that fail silently:

```
INFO  Running startup checks...
INFO  JavaScript runtime for yt-dlp: node
INFO  Highlight models available: openai/gpt-oss-120b, openai/gpt-oss-20b
INFO  Startup checks complete.
```

### Frontend

The frontend is static — no build step. Serve it on **port 5500** (the backend's
CORS allowlist expects it):

```bash
cd clipmaker-frontend
python3 -m http.server 5500
```

Then open <http://127.0.0.1:5500>.

### YouTube sign-in checks

If downloads hit *"Please sign in to confirm you're not a bot"*, export a
`cookies.txt` and point `YTDLP_COOKIES_FILE` at it. Use a throwaway Google
account rather than your main one — automated downloading can get an account
flagged.

---

## Configuration

Everything has a working default; `.env.example` documents the full set. The ones
you are most likely to touch:

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | — | **Required.** Transcription and highlights |
| `GROQ_HIGHLIGHT_MODEL` | `openai/gpt-oss-120b` | Primary ranking model |
| `GROQ_HIGHLIGHT_FALLBACK_MODEL` | `openai/gpt-oss-20b` | Comma-separated fallback chain |
| `MAX_SOURCE_HEIGHT` | `1440` | Cap on downloaded source resolution |
| `OUTPUT_HEIGHT` | `1080` | Drives both output orientations |
| `VIDEO_CRF` / `VIDEO_PRESET` | `18` / `medium` | Encoder quality and speed |
| `MAX_VIDEO_DURATION_SECONDS` | `14400` | 4 hours |
| `MAX_STORAGE_GB` | `20` | Disk ceiling before old jobs are evicted |
| `AUTH_REQUIRED` | `false` | Require a Supabase access token |
| `YTDLP_COOKIES_FILE` | — | Fixes platform sign-in checks |

---

## Housekeeping

Media cleans itself up: expired artifacts, orphaned job folders, and a disk
budget that evicts least-recently-used jobs. It runs at boot and every 30
minutes. To drive it by hand:

```bash
python cleanup_storage.py --dry-run     # what a pass would remove
python cleanup_storage.py --execute     # run it
python cleanup_storage.py --purge-all   # delete all job media (test data)
```

---

## Tests

```bash
cd clipmaker-backend && source venv/bin/activate
python -m unittest discover -s tests
```

214 tests. Several exist specifically because a mistake was already made once —
see [CHANGELOG.md](CHANGELOG.md) for what went wrong and why each check is there.

---

## Architecture

```
clipmaker-backend/
├── main.py                  FastAPI app, lifespan, maintenance loop
├── config.py                every tunable, with documented defaults
├── startup_checks.py        catches silent failures at boot
├── routers/jobs.py          the API surface
└── services/
    ├── platforms.py         which sources we accept, and how each is fetched
    ├── downloader.py        yt-dlp strategy selection and fallback
    ├── transcriber.py       windowed Whisper, word-level timing
    ├── highlight_detector.py  map-reduce ranking and selection
    ├── face_tracker.py      YuNet tracking, crop path planning
    ├── gaming_layout.py     facecam detection, Gameplay + Reaction
    ├── video_cutter.py      FFmpeg filter graphs and encoding
    ├── caption_burner.py    ASS generation, script-aware fonts
    ├── caption_translator.py  phrase-level translation on the timeline
    ├── media_storage.py     job-scoped paths, safe deletion
    ├── cleanup.py           retention, orphan sweep, disk budget
    └── job_store.py         Supabase persistence with memory fallback
```

Working on this codebase? [CLAUDE.md](CLAUDE.md) has the conventions and the traps.

---

## Status

Clipmaker is pre-release and under active development. The pipeline is sound and
well tested, but it has not yet been deployed, load-tested, or run against
concurrent users. See the **Known limits** section of
[CHANGELOG.md](CHANGELOG.md) before relying on it for anything that matters.

---

<p align="center"><sub>Built by Harsh Parmar</sub></p>
