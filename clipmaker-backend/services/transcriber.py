"""
Transcribes a video's audio using Groq's Whisper API, with word-level
timestamps (needed for the word-by-word caption highlight effect later).

Groq's audio file size limit is 25MB on the free tier, so we extract just
the audio track (much smaller than the video) before sending it up.
"""
import subprocess
from pathlib import Path
from groq import Groq
from config import GROQ_API_KEY

client = Groq(api_key=GROQ_API_KEY)


def extract_audio(video_path: Path, audio_path: Path) -> Path:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video_path),
            "-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "64k",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
    )
    return audio_path


def transcribe(video_path: Path, audio_path: Path) -> list[dict]:
    """
    Returns a list of word-level entries:
      [{"word": "hello", "start": 0.12, "end": 0.4}, ...]
    """
    audio_path = extract_audio(video_path, audio_path)

    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            file=(audio_path.name, f.read()),
            model="whisper-large-v3-turbo",
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    words = getattr(response, "words", None) or []
    return [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in words]


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
