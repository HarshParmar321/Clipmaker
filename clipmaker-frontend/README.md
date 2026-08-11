# Clipmaker frontend

Plain HTML/CSS/JS — no build step, no npm needed. Talks to the FastAPI
backend over fetch().

## Run it

1. Make sure the backend is running first (`uvicorn main:app --port 8000`
   in the `clipmaker-backend` folder).
2. From this folder, run a static server:
   ```bash
   python3 -m http.server 5500
   ```
3. Open `http://localhost:5500` in your browser.

If your backend runs on a different host/port, change `API_BASE` at the
top of `script.js`.

## Design notes

- Palette and type choices are documented as CSS custom properties at the
  top of `style.css` (`:root`) — change values there to retheme everything.
- The "filmstrip progress" bar (`.filmstrip-progress` in style.css,
  `renderStageFrames`/`setActiveStage` in script.js) is the signature
  element — it mirrors your actual backend pipeline stages
  (`JobStatus` enum in `services/job_store.py`), so if you add a new
  pipeline stage on the backend, add it to the `STAGES` array in
  `script.js` too and it'll show up automatically.
- Caption style pills are visual-only right now — they don't yet change
  which style gets burned in, since the backend only has one preset
  (`STYLE` dict in `caption_burner.py`). Wire this up once you add more
  presets there.

## Before deploying

- Swap `API_BASE` to your deployed backend URL (Hugging Face Space,
  Railway, etc. — same pattern as your PDF chatbot).
- Tighten the backend's CORS `allow_origins` from `"*"` to your actual
  frontend domain once both are deployed.
- Consider adding real thumbnail frames on the timeline using the
  `extract_thumbnails()` function that already exists in
  `video_cutter.py` on the backend — just needs a route to serve them.
