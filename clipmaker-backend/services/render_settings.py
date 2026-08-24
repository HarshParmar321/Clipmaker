"""Shared, validated settings for generated and edited clips."""

from enum import Enum


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


class CaptionSize(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class ReframeMode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"


TARGET_DIMENSIONS: dict[OutputFormat, tuple[int, int]] = {
    OutputFormat.LANDSCAPE: (1280, 720),
    OutputFormat.VERTICAL: (720, 1280),
}
