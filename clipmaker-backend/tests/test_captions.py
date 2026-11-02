import tempfile
import unittest
from pathlib import Path

from services.caption_burner import _group_words, build_ass_file, retime_caption_text
from services.render_settings import CaptionPosition, CaptionSize, CaptionStyle


class CaptionTests(unittest.TestCase):
    def test_retimes_edited_text_across_clip(self):
        words = [
            {"word": "old", "start": 10.0, "end": 10.5},
            {"word": "copy", "start": 10.5, "end": 11.0},
        ]
        result = retime_caption_text(words, "entirely new caption copy", 10.0, 12.0)
        self.assertEqual([word["word"] for word in result], ["entirely", "new", "caption", "copy"])
        self.assertEqual(result[0]["start"], 10.0)
        self.assertEqual(result[-1]["end"], 12.0)

    def test_ass_uses_requested_canvas_and_style(self):
        words = [{"word": "Hello", "start": 2.0, "end": 2.5}]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "captions.ass"
            build_ass_file(
                words,
                path,
                clip_start_offset=2.0,
                frame_width=1280,
                frame_height=720,
                style=CaptionStyle.CLEAN,
                position=CaptionPosition.TOP,
            )
            content = path.read_text()
        self.assertIn("PlayResX: 1280", content)
        self.assertIn("PlayResY: 720", content)
        self.assertIn("Poppins", content)  # bundled; "Arial" does not exist on Linux
        self.assertIn(",8,", content)

    def test_vertical_caption_uses_mobile_safe_area_and_two_line_wrap(self):
        words = [
            {"word": word, "start": index * 0.25, "end": index * 0.25 + 0.2}
            for index, word in enumerate(["this", "vertical", "subtitle", "stays", "readable"])
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "vertical.ass"
            build_ass_file(words, path, 0, size=CaptionSize.MEDIUM)
            content = path.read_text()
        style_line = next(line for line in content.splitlines() if line.startswith("Style: Caption"))
        self.assertIn(",54,", style_line)
        self.assertIn(",72,72,26,", style_line)
        self.assertIn(r"\N", content)

    def test_landscape_caption_uses_wider_grouping_and_safe_area(self):
        words = [
            {"word": word, "start": index * 0.25, "end": index * 0.25 + 0.2}
            for index, word in enumerate(
                ["landscape", "captions", "can", "fit", "a", "wider", "phrase", "safely"]
            )
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "landscape.ass"
            build_ass_file(
                words,
                path,
                0,
                frame_width=1280,
                frame_height=720,
                position=CaptionPosition.BOTTOM,
            )
            content = path.read_text()
        style_line = next(line for line in content.splitlines() if line.startswith("Style: Caption"))
        self.assertIn(",49,", style_line)
        self.assertIn(",90,90,22,", style_line)
        self.assertIn(r"\N", content)

    def test_caption_events_only_cover_spoken_timing(self):
        words = [
            {"word": "hello", "start": 2.0, "end": 2.4},
            {"word": "there", "start": 2.4, "end": 2.8},
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "timing.ass"
            build_ass_file(words, path, clip_start_offset=1.0)
            dialogue = next(line for line in path.read_text().splitlines() if line.startswith("Dialogue:"))
        self.assertIn("0:00:01.00,0:00:01.80", dialogue)
        self.assertIn("Hello there.", dialogue)

    def test_groups_break_on_long_phrases_and_pauses(self):
        words = [
            {"word": "A", "start": 0, "end": 0.2},
            {"word": "verylongcaptionword", "start": 0.2, "end": 0.6},
            {"word": "Next", "start": 1.4, "end": 1.7},
        ]
        groups = _group_words(
            words, max_words=5, max_characters=18, max_duration=2.6
        )
        self.assertEqual([[item["word"] for item in group] for group in groups], [["A"], ["verylongcaptionword"], ["Next"]])

    def test_karaoke_is_the_only_style_with_word_timing_tags(self):
        words = [{"word": "Hello", "start": 0, "end": 0.5}]
        with tempfile.TemporaryDirectory() as temp:
            bold_path = Path(temp) / "bold.ass"
            karaoke_path = Path(temp) / "karaoke.ass"
            build_ass_file(words, bold_path, 0, style=CaptionStyle.BOLD)
            build_ass_file(words, karaoke_path, 0, style=CaptionStyle.KARAOKE)
            bold = bold_path.read_text()
            karaoke = karaoke_path.read_text()
        self.assertNotIn(r"\kf", bold)
        self.assertIn(r"\kf", karaoke)

    def test_explicit_break_and_join_control_caption_segments(self):
        words = [
            {"word": "one", "start": 0, "end": 0.2, "break_after": True},
            {"word": "two", "start": 0.2, "end": 0.4, "join_next": True},
            {"word": "three", "start": 1.2, "end": 1.4},
        ]
        groups = _group_words(
            words, max_words=5, max_characters=30, max_duration=2.6
        )
        self.assertEqual(
            [[item["word"] for item in group] for group in groups],
            [["one"], ["two", "three"]],
        )


if __name__ == "__main__":
    unittest.main()
