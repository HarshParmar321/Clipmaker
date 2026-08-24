import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import CLEANUP_INTERVAL_MINUTES, SUPABASE_CONFIGURED
from routers import jobs
from services.cleanup import cleanup_expired_artifacts
from services.job_store import recover_interrupted_jobs
from startup_checks import run_startup_checks

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger("clipmaker.main")


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_MINUTES * 60)
        await asyncio.to_thread(cleanup_expired_artifacts)


async def _startup_maintenance(started_at: datetime) -> None:
    """Run network-backed maintenance without delaying API readiness."""
    try:
        recovered = await asyncio.to_thread(recover_interrupted_jobs, started_at)
        if recovered:
            logger.warning("Marked %d interrupted job(s) as retryable", recovered)
        await asyncio.to_thread(cleanup_expired_artifacts)
    except Exception:
        logger.exception("Startup maintenance failed; the API will remain available")


@asynccontextmanager
async def lifespan(_: FastAPI):
    started_at = datetime.now(timezone.utc)
    run_startup_checks()
    maintenance_task = asyncio.create_task(_startup_maintenance(started_at))
    cleanup_task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        maintenance_task.cancel()
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance_task
        with suppress(asyncio.CancelledError):
            await cleanup_task


app = FastAPI(title="Clipmaker API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500", "http://127.0.0.1:5500"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Range"],
    expose_headers=["Accept-Ranges", "Content-Length", "Content-Range"],
)
app.include_router(jobs.router)


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "clipmaker-api",
        "metadata_store": "supabase" if SUPABASE_CONFIGURED else "memory-fallback",
    }
