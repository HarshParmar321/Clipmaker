# Clipmaker frontend

React, TypeScript, Vite, and Motion. The app talks to the FastAPI backend
over fetch while keeping video processing on the backend.

## Run it

1. Make sure the backend is running first (`uvicorn main:app --port 8000`
   in the `clipmaker-backend` folder).
2. Install the frontend packages once:
   ```bash
   npm install
   ```
3. Start the Vite development server:
   ```bash
   npm run dev
   ```
4. Open `http://localhost:5500` in your browser.

If port 8000 is busy during local development, open the frontend with a
validated port override such as `http://localhost:5500/?api_port=8001`.

## Design notes

- Palette and typography are documented as CSS custom properties at the top
  of `src/styles.css`.
- `src/App.tsx` owns job creation, progress polling, retry, cancellation, and
  workspace routing.
- `src/components/Studio.tsx` composes the focused editor: compact clip rail,
  exact canvas preview, settings inspector, trim timeline, and rendering.
- Bold, Clean, and Karaoke caption controls are sent to the backend and match
  the ASS/libass render presets used for exported clips.

## Before deploying

- Swap `API_BASE` to your deployed backend URL (Hugging Face Space,
  Railway, etc. — same pattern as your PDF chatbot).
- Tighten the backend's CORS `allow_origins` from `"*"` to your actual
  frontend domain once both are deployed.
- Set a deployed API base URL through environment configuration before
  production deployment. The current development build targets localhost.
