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


TARGET_DIMENSIONS: dict[OutputFormat, tuple[int, int]] = {
    OutputFormat.LANDSCAPE: (1280, 720),
    OutputFormat.VERTICAL: (720, 1280),
}
