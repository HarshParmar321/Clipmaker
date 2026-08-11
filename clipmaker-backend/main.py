import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from routers import jobs
from startup_checks import run_startup_checks
from config import CLIPS_DIR

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

app = FastAPI(title="Clipmaker API")

# Loosen this to your actual frontend origin before deploying
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router)

# Serves clip videos and timeline thumbnails directly by URL, e.g.
# GET /media/{clip_id}_captioned.mp4 — needed for the trim editor's
# <video> preview and thumbnail strip.
app.mount("/media", StaticFiles(directory=str(CLIPS_DIR)), name="media")


@app.on_event("startup")
def on_startup():
    run_startup_checks()


@app.get("/")
def root():
    return {"status": "ok", "service": "clipmaker-api"}
