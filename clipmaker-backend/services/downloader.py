"""
Downloads a video from any supported platform using yt-dlp.

WHY THIS FILE IS STRUCTURED AS "TRY SEVERAL STRATEGIES":
Platforms actively fight scraping tools like yt-dlp, and which approach
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

The strategy list is per-platform and lives in services/platforms.py.
YouTube is the only one that needs a client ladder - its player clients
see different format catalogues and different anti-bot gates. Everywhere
else a single generic pass is correct, and forcing a YouTube player_client
onto Twitch or Vimeo would be meaningless.

No platform is a dependency. Direct upload is the path that always works,
so every failure here degrades to a clear message pointing at it.
"""
import logging
import os
import subprocess
import time
from pathlib import Path
import yt_dlp
from config import MAX_VIDEO_DURATION_SECONDS, MAX_SOURCE_HEIGHT
from services.exceptions import VideoDownloadError
from services.platforms import (
    YOUTUBE_CLIENT_STRATEGIES,
    Platform,
    platform_for,
)

logger = logging.getLogger("clipmaker.downloader")

# THE single biggest lever on output quality.
#
# YouTube's high-resolution DASH formats are behind a JavaScript signature
# challenge. Without a JS runtime yt-dlp cannot solve it, so it silently falls
# back to the one stream that needs no signature: the 640x360 muxed format.
# That is how a job with MAX_SOURCE_HEIGHT=1440 ends up downloading 360p and
# nothing in the log says why.
#
# yt-dlp enables only "deno" by default. Enabling the rest costs nothing when
# they are absent — it uses the highest-priority runtime that is installed.
# 10MB ranged requests. Small enough that each chunk's URL is still fresh,
# large enough not to add meaningful overhead on a 170MB download.
HTTP_CHUNK_SIZE = int(os.getenv("YTDLP_HTTP_CHUNK_SIZE", 10 * 1024 * 1024))

JS_RUNTIMES = [
    name.strip()
    for name in os.getenv("YTDLP_JS_RUNTIMES", "deno,node,quickjs,bun").split(",")
    if name.strip()
]

COOKIES_BROWSER = os.getenv("YTDLP_COOKIES_BROWSER", "").strip()
COOKIES_FILE = os.getenv("YTDLP_COOKIES_FILE", "").strip()

# Ordered by current real-world reliability, not alphabetically - update
# this list if you notice a strategy consistently failing/succeeding.
# "default" = don't force a client at all, let yt-dlp's own fallback logic
# choose (newer yt-dlp versions do this reasonably well on their own).
# Kept as the YouTube ladder's canonical home for callers that import it.
# Ordered by "best formats we can actually finish downloading", not by "best
# formats offered". YouTube gates video streaming on the web-family clients
# behind a GVS proof-of-origin token; without a PO token provider those clients
# list 1080p, start the transfer, and then 403 partway. The yt-dlp PO Token
# Guide names android_vr, web_embedded and tv (with cookies) as clients that do
# not need one, so they sit right behind "default" as the fallback that can
# still finish. android and ios are last: Google has cut their format ladders
# down, and android often tops out at 360p.
CLIENT_STRATEGIES = list(YOUTUBE_CLIENT_STRATEGIES)


# Errors that mean "ask again", not "this client cannot serve this video".
TRANSIENT_PROBE_MARKERS = (
    "page needs to be reloaded",
    "temporarily",
    "try again",
    "500",
    "502",
    "503",
)


def _build_base_opts(output_dir: Path, platform: Platform | None = None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(output_dir / "original.%(ext)s")
    opts = {
        # The source resolution is the ceiling on final clip quality: a 9:16
        # crop keeps only ~56% of a landscape frame's width, so 720p leaves
        # just 405px to upscale from. Pull the best available up to the cap.
        # Last two branches matter: some clients (ios) only expose HLS, where
        # the height-filtered branches match nothing and yt-dlp gives up with
        # "Requested format is not available" instead of taking what is there.
        "format": (
            f"bestvideo[height<={MAX_SOURCE_HEIGHT}]+bestaudio/"
            f"best[height<={MAX_SOURCE_HEIGHT}]/"
            f"bestvideo+bestaudio/best"
        ),
        "merge_output_format": "mp4",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # YouTube hands out a signed URL, then rejects it partway through a
        # single long GET with "HTTP Error 403: Forbidden" — reliably, around
        # the same few percent of a large file. Chunked ranged requests make
        # yt-dlp re-sign as it goes, which is the standard mitigation.
        "http_chunk_size": HTTP_CHUNK_SIZE,
        "retries": 5,
        "fragment_retries": 10,
        "extractor_retries": 3,
    }
    if JS_RUNTIMES:
        # As a library parameter this is a dict of {runtime: config}, not the
        # comma-separated list the --js-runtimes CLI flag takes. Passing a list
        # raises inside YoutubeDL.__init__, which makes every client strategy
        # fail and surfaces as "we couldn't fetch this video".
        opts["js_runtimes"] = {name: {} for name in JS_RUNTIMES}
    cookies_file = platform.cookies_file() if platform else COOKIES_FILE
    if cookies_file:
        opts["cookiefile"] = cookies_file
    elif COOKIES_BROWSER:
        opts["cookiesfrombrowser"] = (COOKIES_BROWSER,)
    return opts


def _client_opts(output_dir: Path, client: str, platform: Platform | None = None) -> dict:
    opts = _build_base_opts(output_dir, platform)
    # player_client is a YouTube extractor argument. Handing it to Twitch or
    # Vimeo says nothing to their extractors, so only YouTube gets a ladder.
    if client != "default" and (platform is None or platform.key == "youtube"):
        opts["extractor_args"] = {"youtube": {"player_client": [client]}}
    return opts


def _best_available_height(info: dict) -> int:
    """The tallest real video stream this client was offered.

    Different player clients are shown different format ladders for the same
    video: one may expose 1080p DASH while another only sees the 360p muxed
    stream. Knowing this before downloading is the difference between a sharp
    clip and an unusable one.
    """
    heights = [
        int(fmt.get("height") or 0)
        for fmt in (info.get("formats") or [])
        if fmt.get("vcodec") not in (None, "none")
    ]
    return max(heights, default=0)


def _probe_strategy(url: str, output_dir: Path, client: str, platform: Platform | None = None) -> dict:
    """Inspects what one client strategy can offer, without downloading."""
    with yt_dlp.YoutubeDL(_client_opts(output_dir, client, platform)) as ydl:
        info = ydl.extract_info(url, download=False)

        # "only images available" shows up as formats with no real video -
        # treat that the same as a failure so we move to the next strategy
        # instead of downloading a useless storyboard file.
        if _best_available_height(info) == 0:
            raise yt_dlp.utils.DownloadError(f"'{client}' client returned no real video formats")

        duration = info.get("duration") or 0
        if duration > MAX_VIDEO_DURATION_SECONDS:
            # not retryable with a different client - this is a real limit
            raise VideoDownloadError(
                f"That video is {int(duration // 60)} minutes long. "
                f"For now, please use videos under {MAX_VIDEO_DURATION_SECONDS // 60} minutes.",
                retryable=False,
            )
        return info


def _is_transient(error: Exception) -> bool:
    detail = str(error).lower()
    return any(marker in detail for marker in TRANSIENT_PROBE_MARKERS)


def _probe_strategy_with_retry(
    url: str, output_dir: Path, client: str, job_id: str, platform: Platform | None = None
) -> dict:
    """One retry for errors that are about timing rather than capability.

    "The page needs to be reloaded" cost us the tv client on a run where tv was
    the best remaining option, and it succeeds on a second ask.
    """
    try:
        return _probe_strategy(url, output_dir, client, platform)
    except VideoDownloadError:
        raise
    except Exception as exc:
        if not _is_transient(exc):
            raise
        logger.info(f"[{job_id}] '{client}' hit a transient error, retrying once: {exc}")
        time.sleep(1.5)
        return _probe_strategy(url, output_dir, client, platform)


# Below this, a 9:16 crop has so few real pixels that no encoder setting can
# save it, so it is worth spending another probe to look for a better client.
ACCEPTABLE_SOURCE_HEIGHT = 720


def _strategies_for(platform: Platform) -> list[str]:
    """Which passes to try for this platform.

    YouTube reads through the module-level CLIENT_STRATEGIES so there is one
    place to see, change or override the ladder. Every other platform makes a
    single generic pass — see platforms.py for why.
    """
    if platform.key == "youtube":
        return list(CLIENT_STRATEGIES)
    return list(platform.client_strategies) or ["default"]


def _download_failure_message(error: Exception | None, platform: Platform | None = None) -> str:
    """Say which kind of failure this was, and name the right platform.

    A malformed yt-dlp option and a platform refusal used to produce the same
    sentence, which cost a debugging round trip: the message blamed YouTube
    while the real cause was a bad parameter that never reached the network.
    Naming YouTube when the user pasted a Twitch link is the same mistake in a
    different direction.
    """
    detail = str(error or "")
    name = platform.name if platform else "The video platform"

    if isinstance(error, (ValueError, TypeError)):
        return (
            "The downloader is misconfigured, so no request was ever made. "
            "This is a bug on our side, not a problem with the video."
        )
    if "403" in detail or "Forbidden" in detail:
        return (
            f"{name} refused the download partway through. This usually clears "
            "on its own — try again in a minute. If it keeps happening, sign-in "
            "cookies for this platform may have expired, or you can upload the "
            "file directly."
        )
    if platform and platform.login_usually_required:
        return (
            f"We couldn't fetch this {name} video. {name} restricts automated "
            "access heavily, so this often needs sign-in cookies configured. "
            "Uploading the file directly always works."
        )
    return (
        f"We couldn't fetch this video from {name} right now — it may be "
        "private, region-locked, or temporarily blocking automated downloads. "
        "Try again in a few minutes, or upload the file directly instead "
        "(that path never depends on the platform)."
    )


def download_video(url: str, job_id: str, output_dir: Path) -> Path:
    """Pick the best source this video can give us, then actually get it.

    Two separate failures live here and they need different handling. A client
    can be offered a poor format ladder (that is a quality problem — compare
    clients and take the tallest), or it can be handed a signed URL that
    YouTube then rejects mid-transfer with a 403 (that is a reliability
    problem — move to the next client and try again).

    Probing every client up front would be the simplest way to compare, but it
    costs an extra round trip per client on the happy path. So probing stops as
    soon as something good enough turns up, and only resumes if the downloads
    from what we already have all fail.
    """
    platform = platform_for(url)
    last_error: Exception | None = None
    pending = _strategies_for(platform)
    logger.info(f"[{job_id}] source platform: {platform.name}")
    probed: list[tuple[int, str]] = []
    attempted: set[str] = set()
    warned_low = False

    def probe_next_batch() -> list[tuple[int, str]]:
        """Probe clients until one is good enough, or the list runs out."""
        nonlocal last_error
        found: list[tuple[int, str]] = []
        while pending:
            client = pending.pop(0)
            logger.info(f"[{job_id}] probing yt-dlp client strategy: {client}")
            try:
                info = _probe_strategy_with_retry(url, output_dir, client, job_id, platform)
            except VideoDownloadError:
                raise  # not retryable, don't waste time trying other clients
            except Exception as exc:
                logger.warning(f"[{job_id}] client strategy '{client}' failed: {exc}")
                last_error = exc
                continue

            height = _best_available_height(info)
            logger.info(f"[{job_id}] client '{client}' offers up to {height}p")
            found.append((height, client))
            if height >= min(ACCEPTABLE_SOURCE_HEIGHT, MAX_SOURCE_HEIGHT):
                break
        return found

    while True:
        probed.extend(probe_next_batch())
        candidates = [entry for entry in probed if entry[1] not in attempted]
        if not candidates:
            break
        candidates.sort(key=lambda item: item[0], reverse=True)

        if not warned_low and candidates[0][0] < ACCEPTABLE_SOURCE_HEIGHT and not pending:
            warned_low = True
            height = candidates[0][0]
            logger.warning(
                "[%s] Nothing better than %dp was available for this video. Vertical "
                "clips will be soft: a 9:16 crop of %dp leaves about %d pixels "
                "of real width. This is the source, not the encoder.",
                job_id, height, height, round(height * 9 / 16 * 9 / 16),
            )

        for height, client in candidates:
            attempted.add(client)
            logger.info(f"[{job_id}] downloading with client '{client}' at up to {height}p")
            try:
                with yt_dlp.YoutubeDL(_client_opts(output_dir, client, platform)) as ydl:
                    ydl.download([url])
                path = _downloaded_file(output_dir)
                _log_actual_resolution(path, job_id)
                return path
            except Exception as exc:
                logger.warning(f"[{job_id}] download with '{client}' failed: {exc}")
                last_error = exc
                _clear_partials(output_dir)

    _clear_partials(output_dir)
    raise VideoDownloadError(
        user_message=_download_failure_message(last_error, platform),
        technical_detail=str(last_error),
    )


def _log_actual_resolution(path: Path, job_id: str) -> None:
    """What was offered and what landed on disk are different numbers.

    Format selection, merging and client quirks all sit in between, so the only
    honest answer comes from the file itself.
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        size = (result.stdout or "").strip().splitlines()
        if size:
            logger.info(f"[{job_id}] source downloaded: {size[0]}")
    except Exception as exc:  # never fail a good download over a log line
        logger.debug(f"[{job_id}] could not probe the downloaded file: {exc}")


def _clear_partials(output_dir: Path) -> None:
    for partial in output_dir.glob("original.*"):
        partial.unlink(missing_ok=True)


def _downloaded_file(output_dir: Path) -> Path:
    matches = [path for path in output_dir.glob("original.*") if path.suffix != ".part"]
    if not matches:
        raise VideoDownloadError(
            user_message="The download finished but the file went missing. Please try again.",
            technical_detail="No output file found after yt-dlp download",
        )
    return matches[0]
