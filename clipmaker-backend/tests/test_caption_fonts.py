"""A caption font that is not bundled is a production-only bug.

FFmpeg/libass is pointed at fonts/ via fontsdir, but it will still fall back to
whatever the host has installed when a style names something that isn't there.
A developer's Mac has Arial and a Devanagari system font, so Hindi captions
render perfectly in development — and burn in as empty boxes on a Linux server,
where neither exists. The only defence is asserting that every font we name is
a file in the repository.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fontTools.ttLib import TTFont  # noqa: E402

from services.video_cutter import FONTS_DIR  # noqa: E402
from services.caption_burner import (  # noqa: E402
    SCRIPT_FONTS,
    STYLE_PRESETS,
    build_ass_file,
    font_for_text,
)
from services.render_settings import CaptionStyle  # noqa: E402

DEVANAGARI = "यह बहुत अच्छा है"
LATIN = "this is a clip"
CODE_MIXED = "यह बहुत good है"


def _bundled_families() -> dict[str, Path]:
    families: dict[str, Path] = {}
    for path in FONTS_DIR.glob("*.ttf"):
        font = TTFont(path, fontNumber=0, lazy=True)
        for record in font["name"].names:
            if record.nameID == 1:
                families[str(record)] = path
                break
    return families


def _codepoints(path: Path) -> set[int]:
    font = TTFont(path, fontNumber=0, lazy=True)
    points: set[int] = set()
    for table in font["cmap"].tables:
        points |= set(table.cmap.keys())
    return points


class BundledFontTests(unittest.TestCase):
    def test_every_font_a_preset_names_is_bundled(self):
        """The test that would have caught this: 'Arial' was named by two
        presets and has never been in the repository."""
        bundled = _bundled_families()
        for style, preset in STYLE_PRESETS.items():
            with self.subTest(style=style):
                self.assertIn(
                    preset["font"], bundled,
                    f"{style} names {preset['font']!r}, which is not in fonts/",
                )

    def test_every_script_font_is_bundled(self):
        bundled = _bundled_families()
        for _, font in SCRIPT_FONTS:
            self.assertIn(font, bundled, f"{font!r} is not in fonts/")

    def test_the_devanagari_font_can_draw_devanagari(self):
        bundled = _bundled_families()
        points = _codepoints(bundled[font_for_text(DEVANAGARI, "Bebas Neue")])
        missing = [ch for ch in DEVANAGARI.replace(" ", "") if ord(ch) not in points]
        self.assertFalse(missing, f"cannot render {missing}")

    def test_the_devanagari_font_can_also_draw_latin(self):
        """Indian creators code-mix constantly. A Devanagari-only face would
        just move the empty boxes onto the English words."""
        bundled = _bundled_families()
        points = _codepoints(bundled[font_for_text(DEVANAGARI, "Bebas Neue")])
        missing = [ch for ch in "abcXYZ0123?!" if ord(ch) not in points]
        self.assertFalse(missing, f"cannot render {missing}")


class ScriptSelectionTests(unittest.TestCase):
    def test_latin_keeps_the_presets_own_font(self):
        self.assertEqual(font_for_text(LATIN, "Bebas Neue"), "Bebas Neue")

    def test_devanagari_switches_font(self):
        self.assertNotEqual(font_for_text(DEVANAGARI, "Bebas Neue"), "Bebas Neue")

    def test_one_devanagari_word_is_enough_to_switch(self):
        """Mixed text must use the face that can draw both, not the one that
        happens to match the majority of characters."""
        self.assertEqual(
            font_for_text(CODE_MIXED, "Bebas Neue"),
            font_for_text(DEVANAGARI, "Bebas Neue"),
        )

    def test_empty_text_keeps_the_default(self):
        self.assertEqual(font_for_text("", "Bebas Neue"), "Bebas Neue")

    def test_numbers_and_punctuation_do_not_switch_font(self):
        self.assertEqual(font_for_text("100% — (yes!) 2026", "Bebas Neue"), "Bebas Neue")


class RenderedSubtitleTests(unittest.TestCase):
    def _font_in_ass(self, words, style=CaptionStyle.BOLD) -> str:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "captions.ass"
            build_ass_file(words, path, 0, 1080, 1920, style=style)
            line = next(
                line for line in path.read_text(encoding="utf-8").splitlines()
                if line.startswith("Style: Caption")
            )
        return line.split(",")[1]

    def test_a_hindi_clip_burns_with_the_devanagari_face(self):
        words = [{"word": "यह", "start": 0, "end": .5},
                 {"word": "अच्छा", "start": .5, "end": 1.0}]
        self.assertEqual(self._font_in_ass(words), font_for_text(DEVANAGARI, "Bebas Neue"))

    def test_an_english_clip_keeps_the_style_font(self):
        words = [{"word": "hello", "start": 0, "end": .5},
                 {"word": "world", "start": .5, "end": 1.0}]
        self.assertEqual(self._font_in_ass(words), STYLE_PRESETS[CaptionStyle.BOLD]["font"])

    def test_the_decision_looks_at_the_whole_clip_not_the_first_word(self):
        """An English opener followed by Hindi must still pick the Hindi face."""
        words = [{"word": "okay", "start": 0, "end": .4},
                 {"word": "तो", "start": .4, "end": .8},
                 {"word": "सुनो", "start": .8, "end": 1.2}]
        self.assertEqual(self._font_in_ass(words), font_for_text(DEVANAGARI, "Bebas Neue"))


if __name__ == "__main__":
    unittest.main()
