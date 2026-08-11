"""
Builds an .ass subtitle file with word-level highlight animation (the
"bold word pops as it's spoken" style you see in CapCut/Opus Clip), then
burns it into the clip with ffmpeg.

Why .ass and not .srt: .srt only supports static, per-line captions. .ass
supports per-word timing and inline styling via \\k karaoke tags, which is
what makes the word-by-word highlight effect possible without hand-rolling
a canvas overlay.
"""
import subprocess
from pathlib import Path
from config import CLIPS_DIR
from services.exceptions import VideoDownloadError

# One preset for v1, matching the plan to not over-scope caption styling.
STYLE = {
    "font": "Arial Black",
    "size": 22,
    "primary_color": "&H00FFFFFF",   # white (base word color)
    "highlight_color": "&H0000D7FF",  # amber/gold (active word color) - BGR hex
    "outline_color": "&H00000000",   # black outline
    "outline_width": 2,
}

ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{STYLE['font']},{STYLE['size']},{STYLE['primary_color']},{STYLE['highlight_color']},{STYLE['outline_color']},&H00000000,1,0,0,0,100,100,0,0,1,{STYLE['outline_width']},0,2,40,40,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:01d}:{m:02d}:{s:05.2f}"


def _group_into_caption_lines(words: list[dict], max_words_per_line: int = 5) -> list[list[dict]]:
    lines = []
    current = []
    for w in words:
        current.append(w)
        if len(current) >= max_words_per_line:
            lines.append(current)
            current = []
    if current:
        lines.append(current)
    return lines


def build_ass_file(words: list[dict], clip_id: str, clip_start_offset: float) -> Path:
    """
    words: word-level transcript entries with absolute (source-video) timestamps.
    clip_start_offset: the clip's start time in the source video, so we can
    re-base timestamps to be relative to the clip itself (clip time starts at 0).
    """
    ass_path = CLIPS_DIR / f"{clip_id}.ass"
    lines = _group_into_caption_lines(words)

    events = []
    for line_words in lines:
        line_start = line_words[0]["start"] - clip_start_offset
        line_end = line_words[-1]["end"] - clip_start_offset
        if line_start < 0:
            continue

        # \k tags take centiseconds of duration for each word (karaoke highlight)
        karaoke_text = ""
        for w in line_words:
            dur_cs = max(int((w["end"] - w["start"]) * 100), 1)
            karaoke_text += f"{{\\k{dur_cs}}}{w['word']} "

        events.append(
            f"Dialogue: 0,{_fmt_time(line_start)},{_fmt_time(line_end)},Caption,,0,0,0,,{karaoke_text.strip()}"
        )

    ass_path.write_text(ASS_HEADER + "\n".join(events), encoding="utf-8")
    return ass_path


def burn_captions(clip_path: Path, ass_path: Path, clip_id: str) -> Path:
    out_path = CLIPS_DIR / f"{clip_id}_captioned.mp4"
    # ffmpeg's subtitles filter needs escaped colons/backslashes on the path
    escaped_ass = str(ass_path).replace("\\", "\\\\").replace(":", "\\:")
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(clip_path),
            "-vf", f"ass={escaped_ass}",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "copy",
            str(out_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # surface ffmpeg's actual error instead of just an exit code -
        # this is what a raw CalledProcessError swallows by default
        raise VideoDownloadError(
            user_message="Something went wrong while adding captions to this clip.",
            technical_detail=f"ffmpeg exit {result.returncode}: {result.stderr[-2000:]}",
        )
    return out_path
