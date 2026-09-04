"""Create readable, mobile-safe ASS captions with word-level timing."""

from __future__ import annotations

import re
from pathlib import Path

from services.render_settings import CaptionPosition, CaptionSize, CaptionStyle


STYLE_PRESETS = {
    CaptionStyle.BOLD: {
        "font": "Bebas Neue",
        "primary": "&H00FFFFFF",
        "secondary": "&H00FFFFFF",
        "outline": "&H00000000",
        "back": "&H50000000",
        "border": 1,
        "outline_width": 5,
        "shadow": 1,
        "spacing": 0,
        "scale": 1.0,
    },
    CaptionStyle.CLEAN: {
        "font": "Arial",
        "primary": "&H00FFFFFF",
        "secondary": "&H00FFFFFF",
        "outline": "&H00000000",
        "back": "&H8A111111",
        "border": 3,
        "outline_width": 0,
        "shadow": 0,
        "spacing": 0,
        "scale": 0.78,
    },
    CaptionStyle.KARAOKE: {
        "font": "Arial",
        "primary": "&H0028B9FF",
        "secondary": "&H00FFFFFF",
        "outline": "&H00000000",
        "back": "&H50000000",
        "border": 1,
        "outline_width": 3,
        "shadow": 1,
        "spacing": 0,
        "scale": 0.96,
    },
}


def _fmt_time(seconds: float) -> str:
    seconds = max(seconds, 0)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    remainder = seconds % 60
    return f"{hours:01d}:{minutes:02d}:{remainder:05.2f}"


def _escape_ass(text: str) -> str:
    return (
        text.replace("\\", "／")
        .replace("{", "（")
        .replace("}", "）")
        .replace("\n", " ")
        .strip()
    )


TERMINAL_PUNCTUATION = (".", "!", "?", "…")
SIZE_SCALE = {
    CaptionSize.SMALL: 0.84,
    CaptionSize.MEDIUM: 1.0,
    CaptionSize.LARGE: 1.16,
}


def _visible_word(word: dict) -> str:
    return str(word.get("word", "")).strip()


def _group_words(
    words: list[dict],
    *,
    max_words: int,
    max_characters: int,
    max_duration: float,
) -> list[list[dict]]:
    """Group captions by pauses, punctuation, reading length, and duration."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    for word in words:
        text = _visible_word(word)
        if not text:
            continue
        pause = float(word["start"]) - float(current[-1]["end"]) if current else 0
        candidate_characters = sum(len(_visible_word(item)) for item in current) + len(current) + len(text)
        group_duration = float(word["end"]) - float(current[0]["start"]) if current else 0
        previous_ended_sentence = bool(current and _visible_word(current[-1]).endswith(TERMINAL_PUNCTUATION))
        explicit_break = bool(current and current[-1].get("break_after"))
        explicit_join = bool(current and current[-1].get("join_next"))
        if current and not explicit_join and (
            len(current) >= max_words
            or candidate_characters > max_characters
            or group_duration > max_duration
            or pause > 0.58
            or previous_ended_sentence
            or explicit_break
        ):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    return groups


def _caption_tokens(group: list[dict], final_group: bool) -> list[str]:
    tokens = [_escape_ass(_visible_word(word)) for word in group]
    if not tokens:
        return []
    first = tokens[0]
    match = re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", first)
    if match:
        index = match.start()
        tokens[0] = first[:index] + first[index].upper() + first[index + 1 :]
    if final_group and not tokens[-1].endswith(TERMINAL_PUNCTUATION):
        tokens[-1] += "."
    return tokens


def _wrap_tokens(tokens: list[str], max_line_characters: int) -> list[list[str]]:
    if not tokens:
        return []
    lines: list[list[str]] = [[]]
    for token in tokens:
        proposed = len(" ".join([*lines[-1], token]))
        if lines[-1] and proposed > max_line_characters and len(lines) < 2:
            lines.append([])
        lines[-1].append(token)
    return lines


def retime_caption_text(
    original_words: list[dict], caption_text: str | None, start: float, end: float
) -> list[dict]:
    """Apply user-edited copy while retaining timings whenever possible."""
    if not caption_text or not caption_text.strip():
        return original_words
    tokens = re.findall(r"\S+", caption_text.strip())
    if not tokens:
        return []
    if len(tokens) == len(original_words):
        return [
            {**word, "word": token}
            for word, token in zip(original_words, tokens, strict=True)
        ]

    duration = max(end - start, 0.1)
    step = duration / len(tokens)
    return [
        {
            "word": token,
            "start": start + index * step,
            "end": min(start + (index + 1) * step, end),
        }
        for index, token in enumerate(tokens)
    ]


def build_ass_file(
    words: list[dict],
    ass_path: Path,
    clip_start_offset: float,
    frame_width: int = 720,
    frame_height: int = 1280,
    style: CaptionStyle = CaptionStyle.BOLD,
    position: CaptionPosition = CaptionPosition.BOTTOM,
    size: CaptionSize = CaptionSize.MEDIUM,
) -> Path:
    """Build subtitles whose typography scales with the rendered frame."""
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    style = CaptionStyle(style)
    position = CaptionPosition(position)
    size = CaptionSize(size)
    preset = STYLE_PRESETS[style]
    is_vertical = frame_height > frame_width
    # Width is the limiting dimension in portrait video. Basing type on height
    # made 9:16 captions huge and caused long words to leave the safe area.
    base_size = frame_width * (0.075 if is_vertical else 0.038)
    font_size = round(
        max(28, min(base_size * preset["scale"] * SIZE_SCALE[size], 72))
    )
    max_words = 5 if is_vertical else 8
    max_characters = 30 if is_vertical else 52
    max_line_characters = 18 if is_vertical else 34
    max_duration = 2.6 if is_vertical else 3.2
    alignment = {
        CaptionPosition.TOP: 8,
        CaptionPosition.CENTER: 5,
        CaptionPosition.BOTTOM: 2,
        CaptionPosition.GAMING: 2,
    }[position]
    if position == CaptionPosition.GAMING:
        # The gameplay/reaction split is at 65% of a portrait canvas. ASS
        # bottom alignment measures upward from the bottom, so this places the
        # caption just above that boundary without covering HUD information.
        margin_v = round(frame_height * 0.34)
    elif position == CaptionPosition.BOTTOM:
        # ASS anchors MarginV at the font baseline, so the visible glyph box
        # sits roughly another font-height above it. A small baseline margin
        # visually matches the browser's 8%/6% bottom box offset.
        margin_v = round(frame_height * (0.02 if is_vertical else 0.03))
    elif position == CaptionPosition.TOP:
        margin_v = round(frame_height * (0.13 if is_vertical else 0.08))
    else:
        margin_v = 0
    margin_h = round(frame_width * (0.10 if is_vertical else 0.07))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {frame_width}
PlayResY: {frame_height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{preset['font']},{font_size},{preset['primary']},{preset['secondary']},{preset['outline']},{preset['back']},1,0,0,0,100,100,{preset['spacing']},0,{preset['border']},{preset['outline_width']},{preset['shadow']},{alignment},{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events: list[str] = []
    groups = _group_words(
        words,
        max_words=max_words,
        max_characters=max_characters,
        max_duration=max_duration,
    )
    for group_index, group in enumerate(groups):
        line_start = max(float(group[0]["start"]) - clip_start_offset, 0)
        line_end = max(float(group[-1]["end"]) - clip_start_offset, line_start + 0.08)
        tokens = _caption_tokens(group, group_index == len(groups) - 1)
        token_index = 0
        rendered_lines: list[str] = []
        for line in _wrap_tokens(tokens, max_line_characters):
            parts: list[str] = []
            for token in line:
                if style == CaptionStyle.KARAOKE:
                    word = group[token_index]
                    word_start = float(word["start"])
                    next_start = (
                        float(group[token_index + 1]["start"])
                        if token_index + 1 < len(group)
                        else float(word["end"])
                    )
                    duration_cs = max(
                        round((max(next_start, float(word["end"])) - word_start) * 100),
                        1,
                    )
                    parts.append(f"{{\\kf{duration_cs}}}{token}")
                else:
                    parts.append(token)
                token_index += 1
            rendered_lines.append(" ".join(parts))
        caption_text = r"\N".join(rendered_lines)
        events.append(
            f"Dialogue: 0,{_fmt_time(line_start)},{_fmt_time(line_end)},Caption,,0,0,0,,"
            f"{{\\fad(60,80)}}{caption_text}"
        )

    ass_path.write_text(header + "\n".join(events), encoding="utf-8")
    return ass_path
