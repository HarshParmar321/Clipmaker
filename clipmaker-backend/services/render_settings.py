"""Shared, validated settings for generated and edited clips."""

from enum import Enum

from config import OUTPUT_HEIGHT


class OutputFormat(str, Enum):
    ORIGINAL = "original"
    LANDSCAPE = "landscape"
    VERTICAL = "vertical"


class CaptionStyle(str, Enum):
    BOLD = "bold"
    CLEAN = "clean"
    KARAOKE = "karaoke"


class CaptionPosition(str, Enum):
    TOP = "top"
    CENTER = "center"
    BOTTOM = "bottom"
    GAMING = "gaming"


class CaptionSize(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class ReframeMode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"


class ContentMode(str, Enum):
    GENERAL = "general"
    GAMING = "gaming"


class LayoutMode(str, Enum):
    SMART = "smart"
    GAMEPLAY_REACTION = "gameplay_reaction"


class FacecamCorner(str, Enum):
    AUTO = "auto"
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"


def _even(value: float) -> int:
    """H.264 requires even dimensions."""
    return max(2, round(value / 2) * 2)


# Derived from OUTPUT_HEIGHT so one setting moves both orientations:
# 1080 -> 1920x1080 landscape and 1080x1920 vertical.
_LANDSCAPE_HEIGHT = _even(OUTPUT_HEIGHT)
_VERTICAL_WIDTH = _even(OUTPUT_HEIGHT)
TARGET_DIMENSIONS: dict[OutputFormat, tuple[int, int]] = {
    OutputFormat.LANDSCAPE: (_even(_LANDSCAPE_HEIGHT * 16 / 9), _LANDSCAPE_HEIGHT),
    OutputFormat.VERTICAL: (_VERTICAL_WIDTH, _even(_VERTICAL_WIDTH * 16 / 9)),
}
