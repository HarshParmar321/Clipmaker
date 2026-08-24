"""
Downloads a video from a URL (YouTube etc.) using yt-dlp.

WHY THIS FILE IS STRUCTURED AS "TRY SEVERAL STRATEGIES":
YouTube actively fights scraping tools like yt-dlp, and which approach
works can change week to week, differ per video, or differ per account.
There is no single flag that "permanently fixes" this - both the yt-dlp
maintainers and every product built on top of it (including commercial
tools like Opus Clip) deal with this as an ongoing reality, not a
one-time bug.

So instead of hardcoding one client and hoping, we try a short list of
known-working strategies in order, log which one succeeds, and only
raise a clean, honest error to the user if every strategy fails. This is
the same pattern production scraping-adjacent tools use: graceful
degradation, not a single point of failure.

For v1, keep this to YouTube only - other platforms (Instagram, Twitter/X)
need auth cookies and break often when the platform changes its API.
"""
import logging
import os
from pathlib import Path
import yt_dlp
from config import MAX_VIDEO_DURATION_SECONDS
from services.exceptions import VideoDownloadError

logger = logging.getLogger("clipmaker.downloader")

COOKIES_BROWSER = os.getenv("YTDLP_COOKIES_BROWSER", "").strip()
COOKIES_FILE = os.getenv("YTDLP_COOKIES_FILE", "").strip()

# Ordered by current real-world reliability, not alphabetically - update
# this list if you notice a strategy consistently failing/succeeding.
# "default" = don't force a client at all, let yt-dlp's own fallback logic
# choose (newer yt-dlp versions do this reasonably well on their own).
CLIENT_STRATEGIES = ["default", "tv", "android", "ios"]


def _build_base_opts(output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(output_dir / "original.%(ext)s")
    opts = {
        "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "merge_output_format": "mp4",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    elif COOKIES_BROWSER:
        opts["cookiesfrombrowser"] = (COOKIES_BROWSER,)
    return opts


def _try_strategy(url: str, output_dir: Path, client: str) -> dict:
    """Attempts one client strategy. Returns yt-dlp's info dict on success,
    raises on failure (caller decides whether to try the next strategy)."""
    opts = _build_base_opts(output_dir)
    if client != "default":
        opts["extractor_args"] = {"youtube": {"player_client": [client]}}

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

        # "only images available" shows up as formats with no real video -
        # treat that the same as a failure so we move to the next strategy
        # instead of downloading a useless storyboard file.
        formats = info.get("formats") or []
        has_real_video = any(f.get("vcodec") not in (None, "none") for f in formats)
        if not has_real_video:
            raise yt_dlp.utils.DownloadError(f"'{client}' client returned no real video formats")

        duration = info.get("duration") or 0
        if duration > MAX_VIDEO_DURATION_SECONDS:
            # not retryable with a different client - this is a real limit
            raise VideoDownloadError(
                f"That video is {int(duration // 60)} minutes long. "
                f"For now, please use videos under {MAX_VIDEO_DURATION_SECONDS // 60} minutes.",
                retryable=False,
            )

        ydl.download([url])
        return info


def download_video(url: str, job_id: str, output_dir: Path) -> Path:
    last_error = None

    for client in CLIENT_STRATEGIES:
        try:
            logger.info(f"[{job_id}] trying yt-dlp client strategy: {client}")
            _try_strategy(url, output_dir, client)
            logger.info(f"[{job_id}] succeeded with client strategy: {client}")
            break
        except VideoDownloadError:
            raise  # not retryable, don't waste time trying other clients
        except Exception as e:
            logger.warning(f"[{job_id}] client strategy '{client}' failed: {e}")
            last_error = e
            continue
    else:
        # every strategy in CLIENT_STRATEGIES failed
        for partial in output_dir.glob("original.*"):
            partial.unlink(missing_ok=True)
        raise VideoDownloadError(
            user_message=(
                "We couldn't fetch this video right now — YouTube may be "
                "temporarily blocking automated downloads for it. Try a "
                "different video, try again in a few minutes, or upload "
                "the file directly instead (that path never depends on YouTube)."
            ),
            technical_detail=str(last_error),
        )

    matches = [path for path in output_dir.glob("original.*") if path.suffix != ".part"]
    if not matches:
        raise VideoDownloadError(
            user_message="The download finished but the file went missing. Please try again.",
            technical_detail="No output file found after yt-dlp download",
        )
    return matches[0]
