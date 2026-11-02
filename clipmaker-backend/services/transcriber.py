"""
Transcribes a video's audio using Groq's Whisper API, with word-level
timestamps (needed for the word-by-word caption highlight effect later).

WHY THIS CHUNKS:
Groq's audio upload limit is 25MB. At 64kbps mono that is about 52 minutes of
speech, which was a hard ceiling nothing downstream could work around — a
two-hour podcast or a four-hour stream simply failed. So the audio is cut into
windows and transcribed one at a time.

The subtlety is the seam. Whisper is much worse at the first and last words of
a clip than in the middle, because it has no context there. So each window is
extracted with a few seconds of lead-in overlap, giving Whisper context across
the boundary — but every word is then assigned to exactly one window by its
absolute start time, so nothing is transcribed twice. The overlap exists to
improve accuracy, not to be stitched.
"""
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from groq import Groq

from config import (
    GROQ_API_KEY,
    TRANSCRIBE_CHUNK_SECONDS,
    TRANSCRIBE_OVERLAP_SECONDS,
)

logger = logging.getLogger("clipmaker.transcriber")

client = Groq(api_key=GROQ_API_KEY)

WHISPER_MODEL = "whisper-large-v3-turbo"
# Groq's limit is 25MB. At 64kbps a 10-minute window is about 4.8MB, so there
# is a wide margin — but a wildly long window would silently blow past it.
GROQ_UPLOAD_LIMIT_BYTES = 25 * 1024 * 1024

ProgressCallback = Callable[[int, int], None]


def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run(args, check=True, capture_output=True)


def extract_audio(
    video_path: Path,
    audio_path: Path,
    *,
    start: float | None = None,
    duration: float | None = None,
) -> Path:
    """Pull a speech-optimised mono track, optionally just one window of it."""
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-y"]
    if start is not None:
        # Before -i, so ffmpeg seeks instead of decoding everything up to here.
        args += ["-ss", f"{max(0.0, start):.3f}"]
    args += ["-i", str(video_path)]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += [
        "-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "64k",
        str(audio_path),
    ]
    _run_ffmpeg(args)
    return audio_path


def probe_audio_duration(video_path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, check=True,
    )
    try:
        return float((result.stdout or "0").strip())
    except ValueError:
        return 0.0


def plan_windows(
    total_seconds: float,
    *,
    chunk_seconds: float = TRANSCRIBE_CHUNK_SECONDS,
    overlap_seconds: float = TRANSCRIBE_OVERLAP_SECONDS,
) -> list[tuple[float, float, float]]:
    """Return (extract_from, extract_duration, keep_words_from) per window.

    `keep_words_from` is what de-duplicates: a window is extracted early to give
    Whisper context, but only owns the words that actually start within its own
    slice of the timeline.
    """
    if total_seconds <= 0:
        return []
    chunk = max(30.0, float(chunk_seconds))
    overlap = max(0.0, min(float(overlap_seconds), chunk / 2))
    if total_seconds <= chunk:
        return [(0.0, total_seconds, 0.0)]

    windows: list[tuple[float, float, float]] = []
    boundary = 0.0
    while boundary < total_seconds:
        extract_from = max(0.0, boundary - overlap)
        extract_until = min(total_seconds, boundary + chunk)
        windows.append((extract_from, extract_until - extract_from, boundary))
        boundary += chunk
    return windows


def _transcribe_file(audio_path: Path) -> list[dict]:
    size = audio_path.stat().st_size
    if size > GROQ_UPLOAD_LIMIT_BYTES:
        raise ValueError(
            f"Audio window is {size / 1024 ** 2:.1f} MB, over Groq's "
            f"{GROQ_UPLOAD_LIMIT_BYTES / 1024 ** 2:.0f} MB limit. "
            "Lower TRANSCRIBE_CHUNK_SECONDS."
        )
    with open(audio_path, "rb") as handle:
        response = client.audio.transcriptions.create(
            file=(audio_path.name, handle.read()),
            model=WHISPER_MODEL,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )
    return list(getattr(response, "words", None) or [])


def transcribe(
    video_path: Path,
    audio_path: Path,
    *,
    on_progress: ProgressCallback | None = None,
) -> list[dict]:
    """
    Returns a list of word-level entries on the video's own timeline:
      [{"word": "hello", "start": 0.12, "end": 0.4}, ...]
    """
    total_seconds = probe_audio_duration(video_path)
    windows = plan_windows(total_seconds)

    if len(windows) <= 1:
        # Short video: one extraction, one request, no seams to worry about.
        extract_audio(video_path, audio_path)
        if on_progress:
            on_progress(1, 1)
        return [
            {"word": word["word"], "start": word["start"], "end": word["end"]}
            for word in _transcribe_file(audio_path)
        ]

    logger.info(
        "Transcribing %.1f minutes in %d windows of %.0fs",
        total_seconds / 60, len(windows), TRANSCRIBE_CHUNK_SECONDS,
    )

    words: list[dict] = []
    for index, (extract_from, duration, keep_from) in enumerate(windows, start=1):
        window_path = audio_path.with_name(f"{audio_path.stem}.{index:03d}{audio_path.suffix}")
        try:
            extract_audio(video_path, window_path, start=extract_from, duration=duration)
            for word in _transcribe_file(window_path):
                start = float(word["start"]) + extract_from
                # Each word belongs to exactly one window. The overlap was for
                # Whisper's benefit, not ours.
                if start < keep_from and index > 1:
                    continue
                words.append({
                    "word": word["word"],
                    "start": round(start, 3),
                    "end": round(float(word["end"]) + extract_from, 3),
                })
        finally:
            window_path.unlink(missing_ok=True)
        if on_progress:
            on_progress(index, len(windows))
        logger.info("Transcribed window %d/%d (%d words so far)", index, len(windows), len(words))

    words.sort(key=lambda word: word["start"])
    return words


def words_to_plain_transcript(words: list[dict]) -> str:
    """Flattens word-level data into a readable, timestamped transcript
    for feeding to the highlight-detection LLM prompt."""
    lines = []
    bucket = []
    bucket_start = None
    for w in words:
        if bucket_start is None:
            bucket_start = w["start"]
        bucket.append(w["word"])
        if len(bucket) >= 15:
            lines.append(f"[{bucket_start:.1f}s] {' '.join(bucket)}")
            bucket = []
            bucket_start = None
    if bucket:
        lines.append(f"[{bucket_start:.1f}s] {' '.join(bucket)}")
    return "\n".join(lines)
