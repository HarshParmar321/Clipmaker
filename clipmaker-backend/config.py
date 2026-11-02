import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    return value


def _float_env(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    return value


def _bool_env(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
# Either setting may hold a comma-separated list; they are tried in order.
# The gpt-oss models are reasoning models: on Groq they put their answer in a
# separate field and leave `content` empty, which surfaces as
# json_validate_failed with an empty failed_generation. Qwen3 can switch
# thinking off entirely, so it leads the chain.
_DEFAULT_HIGHLIGHT_MODEL = "qwen/qwen3.8-27b"
_DEFAULT_HIGHLIGHT_FALLBACK = "openai/gpt-oss-120b,openai/gpt-oss-20b"
GROQ_HIGHLIGHT_MODEL = (
    os.getenv("GROQ_HIGHLIGHT_MODEL", _DEFAULT_HIGHLIGHT_MODEL).strip()
    or _DEFAULT_HIGHLIGHT_MODEL
)
GROQ_HIGHLIGHT_FALLBACK_MODEL = (
    os.getenv("GROQ_HIGHLIGHT_FALLBACK_MODEL", _DEFAULT_HIGHLIGHT_FALLBACK).strip()
    or _DEFAULT_HIGHLIGHT_FALLBACK
)
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_SERVER_KEY = SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY
AUTH_REQUIRED = _bool_env("AUTH_REQUIRED")

storage_setting = Path(os.getenv("STORAGE_DIR", "./storage"))
STORAGE_DIR = (storage_setting if storage_setting.is_absolute() else BASE_DIR / storage_setting).resolve()
JOBS_DIR = STORAGE_DIR / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

# ── Output quality ────────────────────────────────────────────────────
# A 9:16 clip is a narrow column cut out of a landscape frame, so the source
# resolution — not the encoder — decides how sharp the result can be:
#   720p source  -> 405px of usable width  -> 2.7x upscale to 1080. Soft.
#   1440p source -> 810px                  -> 1.33x. Acceptable.
#   4K source    -> 1215px                 -> a downscale. Sharp.
# Downloading higher costs disk and time, so it is tunable.
MAX_SOURCE_HEIGHT = _int_env("MAX_SOURCE_HEIGHT", 1440, minimum=360)
OUTPUT_HEIGHT = _int_env("OUTPUT_HEIGHT", 1080, minimum=480)
VIDEO_CRF = _int_env("VIDEO_CRF", 18, minimum=0)
VIDEO_PRESET = os.getenv("VIDEO_PRESET", "medium").strip() or "medium"
AUDIO_BITRATE = os.getenv("AUDIO_BITRATE", "192k").strip() or "192k"

# A 9:16 crop of a landscape frame already upscales before any zoom is applied.
# Zooming multiplies that. This is the total upscale we are willing to ship, and
# it is what decides how far smart reframe may push in on a given source:
#   1440p source -> zoom ceiling 1.50    1080p -> 1.12    720p -> 1.00 (none)
MAX_UPSCALE_FACTOR = _float_env("MAX_UPSCALE_FACTOR", 2.0, minimum=1.0)

MAX_UPLOAD_BYTES = _int_env("MAX_UPLOAD_BYTES", 500 * 1024 * 1024)
# Transcription windows the audio and highlight detection windows the
# transcript, so neither stage has a length ceiling any more. What remains is
# patience: a four-hour stream is real processing time, not a hung job.
MAX_VIDEO_DURATION_SECONDS = _int_env("MAX_VIDEO_DURATION_SECONDS", 4 * 60 * 60)
FAILED_JOB_TTL_HOURS = _int_env("FAILED_JOB_TTL_HOURS", 1)
THUMBNAIL_TTL_HOURS = _int_env("THUMBNAIL_TTL_HOURS", 24)
SOURCE_VIDEO_TTL_HOURS = _int_env("SOURCE_VIDEO_TTL_HOURS", 24)
FINAL_CLIP_TTL_DAYS = _int_env("FINAL_CLIP_TTL_DAYS", 7)
CLEANUP_INTERVAL_MINUTES = _int_env("CLEANUP_INTERVAL_MINUTES", 30)
CLEANUP_DRY_RUN = _bool_env("CLEANUP_DRY_RUN")

# ── Disk budget ───────────────────────────────────────────────────────
# TTL cleanup is time-based, so it cannot save you from a burst: twenty
# 5 GB source videos arriving in one hour fill the disk while every one of
# them is still hours away from expiring. This is the ceiling that actually
# holds, and it matters more the longer the videos we accept get.
MAX_STORAGE_GB = _float_env("MAX_STORAGE_GB", 20.0, minimum=1.0)

# Files belonging to a job that no longer exists in the database — a crashed
# run, a manual test, a wiped table — are invisible to TTL cleanup forever,
# because cleanup works from artifact rows. The grace period keeps a job that
# is mid-flight (and not yet written to the database) from being swept.
ORPHAN_GRACE_HOURS = _int_env("ORPHAN_GRACE_HOURS", 2)

# ── Transcription windows ─────────────────────────────────────────────
# Groq caps audio uploads at 25MB, which at 64kbps mono is about 52 minutes.
# That was a hard ceiling on the whole product: podcasts and streams simply
# failed. Audio is now transcribed in windows, with a little overlap so Whisper
# has context across each seam (it is noticeably worse at the first and last
# words of a clip). A 10-minute window at 64kbps is ~4.8MB — a wide margin.
TRANSCRIBE_CHUNK_SECONDS = _int_env("TRANSCRIBE_CHUNK_SECONDS", 600, minimum=30)
TRANSCRIBE_OVERLAP_SECONDS = _int_env("TRANSCRIBE_OVERLAP_SECONDS", 4, minimum=0)

# ── Highlight detection windows ───────────────────────────────────────
# A three-hour podcast is roughly 27,000 words. Asked to find five moments in
# all of it at once, a model either refuses the context or skims and returns
# something generic. Splitting the transcript means each request is a size the
# model actually reads, and a later pass ranks all the candidates globally.
# ~2,500 words is about 17 minutes of speech.
HIGHLIGHT_WINDOW_WORDS = _int_env("HIGHLIGHT_WINDOW_WORDS", 2500, minimum=400)
# Enough shared text that a moment sitting on a boundary is fully visible to
# one of the two windows rather than cut in half by both.
HIGHLIGHT_OVERLAP_WORDS = _int_env("HIGHLIGHT_OVERLAP_WORDS", 200, minimum=0)

SUPABASE_CONFIGURED = bool(
    SUPABASE_URL
    and SUPABASE_SERVER_KEY
    and "your-project" not in SUPABASE_URL
    and "your_backend_key" not in SUPABASE_SERVER_KEY
    and "your_legacy" not in SUPABASE_SERVER_KEY
)
