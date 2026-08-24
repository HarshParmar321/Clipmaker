"""
Runs a couple of cheap sanity checks when the server boots, so missing
system dependencies (like ffmpeg lacking libass) show up immediately and
loudly in the logs - instead of surfacing 20 minutes into a real user's
pipeline run, several stages deep.
"""
import logging
import importlib.util
import shutil
import subprocess

from config import AUTH_REQUIRED, SUPABASE_CONFIGURED

logger = logging.getLogger("clipmaker.startup")


def check_ffmpeg_installed() -> bool:
    if shutil.which("ffmpeg") is None:
        logger.error(
            "ffmpeg was not found on PATH. Install it: "
            "brew install ffmpeg (Mac) / apt install ffmpeg (Linux)."
        )
        return False
    return True


def check_libass_support() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-filters"], capture_output=True, text=True, timeout=10
        )
        has_ass = any(
            line.strip().split() and line.strip().split()[1] == "ass"
            for line in result.stdout.splitlines()
            if len(line.strip().split()) > 1
        )
        if not has_ass:
            logger.warning(
                "Your ffmpeg build does not support the 'ass' filter "
                "(no libass support) - caption burning will fail. "
                "On Mac: brew tap homebrew-ffmpeg/ffmpeg && "
                "brew install homebrew-ffmpeg/ffmpeg/ffmpeg-full"
            )
        return has_ass
    except Exception as e:
        logger.warning(f"Could not verify ffmpeg's ass filter support: {e}")
        return True  # don't block startup over a check that itself failed


def run_startup_checks() -> None:
    logger.info("Running startup checks...")
    ffmpeg_ok = check_ffmpeg_installed()
    if ffmpeg_ok:
        check_libass_support()
    if not SUPABASE_CONFIGURED:
        logger.warning(
            "Supabase metadata persistence is not configured. The API will use "
            "an in-memory fallback until SUPABASE_URL and "
            "SUPABASE_SECRET_KEY are added to .env."
        )
    if AUTH_REQUIRED and not SUPABASE_CONFIGURED:
        logger.error("AUTH_REQUIRED=true cannot work until Supabase is configured.")
    if importlib.util.find_spec("cv2") is None:
        logger.warning(
            "OpenCV is not installed; vertical clips will use centred reframing. "
            "Install requirements.txt to enable automatic face tracking."
        )
    logger.info("Startup checks complete.")
