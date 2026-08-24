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


def _bool_env(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_HIGHLIGHT_MODEL = (
    os.getenv("GROQ_HIGHLIGHT_MODEL", "openai/gpt-oss-120b").strip()
    or "openai/gpt-oss-120b"
)
GROQ_HIGHLIGHT_FALLBACK_MODEL = (
    os.getenv("GROQ_HIGHLIGHT_FALLBACK_MODEL", "openai/gpt-oss-20b").strip()
    or "openai/gpt-oss-20b"
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

MAX_UPLOAD_BYTES = _int_env("MAX_UPLOAD_BYTES", 500 * 1024 * 1024)
MAX_VIDEO_DURATION_SECONDS = _int_env("MAX_VIDEO_DURATION_SECONDS", 15 * 60)
FAILED_JOB_TTL_HOURS = _int_env("FAILED_JOB_TTL_HOURS", 1)
THUMBNAIL_TTL_HOURS = _int_env("THUMBNAIL_TTL_HOURS", 24)
SOURCE_VIDEO_TTL_HOURS = _int_env("SOURCE_VIDEO_TTL_HOURS", 24)
FINAL_CLIP_TTL_DAYS = _int_env("FINAL_CLIP_TTL_DAYS", 7)
CLEANUP_INTERVAL_MINUTES = _int_env("CLEANUP_INTERVAL_MINUTES", 30)
CLEANUP_DRY_RUN = _bool_env("CLEANUP_DRY_RUN")

SUPABASE_CONFIGURED = bool(
    SUPABASE_URL
    and SUPABASE_SERVER_KEY
    and "your-project" not in SUPABASE_URL
    and "your_backend_key" not in SUPABASE_SERVER_KEY
    and "your_legacy" not in SUPABASE_SERVER_KEY
)
