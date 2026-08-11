# Clipmaker

AI tool that turns a long video into short, captioned, clip-worthy segments
— with a trim editor so you can adjust each clip's start/end and toggle
captions on or off before exporting.

## Project structure

```
clipmaker/
├── clipmaker-backend/     FastAPI server
│   ├── main.py                  boots server, serves /media static files
│   ├── startup_checks.py        catches missing ffmpeg/libass immediately
│   ├── routers/jobs.py          job creation, status, thumbnails, render
│   ├── services/
│   │   ├── downloader.py        multi-strategy YouTube downloader
│   │   ├── exceptions.py        clean, user-facing error types
│   │   ├── transcriber.py       Groq Whisper, word-level timestamps
│   │   ├── highlight_detector.py Groq LLaMA picks clip-worthy segments
│   │   ├── video_cutter.py      ffmpeg cutting + thumbnail extraction
│   │   ├── caption_burner.py    word-by-word animated caption burn-in
│   │   └── pipeline.py          orchestrates the full job
│   └── README.md
│
└── clipmaker-frontend/    plain HTML/CSS/JS, no build step
    ├── index.html                includes the trim editor panel
    ├── style.css
    ├── script.js                  drag-to-trim + caption toggle logic
    └── README.md
```

## What's new: the trim editor

Click **"Edit clip"** on any generated clip to open a trim editor:
- **Timeline with frame thumbnails** — drag the amber handles to adjust
  where the clip starts and ends.
- **Captions toggle** — on by default; switch off to export without
  captions.
- **Export edit** — re-cuts and (optionally) re-capions from the
  *original* source video, so quality never degrades from re-editing a
  clip. The original clip is untouched — exports are non-destructive.

This was tested end-to-end against real ffmpeg output (not just
route-registration checks) before being handed over: real cutting, real
caption burn-in with captions both on and off, real thumbnail
extraction, and confirmed the clip preview actually plays inline in a
browser rather than forcing a download.

## Run both together

Open two terminals.

**Terminal 1 — backend:**
```bash
cd clipmaker-backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add your Groq API key
uvicorn main:app --reload --port 8000
```

**Terminal 2 — frontend:**
```bash
cd clipmaker-frontend
python3 -m http.server 5500
```

Open **http://localhost:5500**.

## Known issues already solved (see full history in this project)

- **YouTube "Please sign in" / format errors** — `downloader.py` tries
  multiple client strategies automatically and fails cleanly with a
  helpful message if all are blocked. Upload is the fully reliable path.
- **ffmpeg captions failing silently** — fixed by capturing real ffmpeg
  stderr on error, and a startup check that warns immediately if your
  `ffmpeg` lacks `libass` support (common on Mac with the default
  Homebrew formula — fix: `brew install homebrew-ffmpeg/ffmpeg/ffmpeg`
  after tapping `homebrew-ffmpeg/ffmpeg`).
- **Clips not playing in browser** — was caused by the download endpoint
  forcing `Content-Disposition: attachment`. Clip previews now use a
  separate `/media` static route with no forced download.

## Common setup issues

- **`pip: command not found`** — activate the venv first:
  `python3 -m venv venv && source venv/bin/activate`
- **Caption burning fails** — check `ffmpeg -filters | grep -i "\bass\b"`
  returns a result. If not, see the libass fix above.
