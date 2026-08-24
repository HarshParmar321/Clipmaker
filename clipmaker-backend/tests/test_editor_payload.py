import unittest

from pydantic import ValidationError

from routers.jobs import RenderClipRequest


class EditorPayloadTests(unittest.TestCase):
    def test_accepts_ordered_caption_words_inside_trimmed_clip(self):
        payload = RenderClipRequest(
            start=2,
            end=7,
            caption_words=[
                {"word": "Hello", "start": 0, "end": 0.4},
                {"word": "world", "start": 0.5, "end": 1.0, "break_after": True},
            ],
        )
        self.assertEqual(payload.caption_words[1].word, "world")
        self.assertTrue(payload.caption_words[1].break_after)

    def test_rejects_caption_word_outside_trimmed_clip(self):
        with self.assertRaises(ValidationError):
            RenderClipRequest(
                start=2,
                end=4,
                caption_words=[{"word": "Late", "start": 1.8, "end": 2.5}],
            )

    def test_rejects_unordered_caption_words(self):
        with self.assertRaises(ValidationError):
            RenderClipRequest(
                start=0,
                end=5,
                caption_words=[
                    {"word": "Second", "start": 1, "end": 1.3},
                    {"word": "First", "start": 0.2, "end": 0.5},
                ],
            )

    def test_accepts_ordered_reframe_keyframes_and_zoom(self):
        payload = RenderClipRequest(
            start=2,
            end=12,
            crop_y=0.25,
            crop_zoom=1.4,
            crop_keyframes=[
                {"time": 0, "x": 0.2, "y": 0.4},
                {"time": 10, "x": 0.8, "y": 0.6},
            ],
        )
        self.assertEqual(payload.crop_zoom, 1.4)
        self.assertEqual(payload.crop_keyframes[1].x, 0.8)

    def test_rejects_unordered_or_out_of_range_reframe_keyframes(self):
        with self.assertRaises(ValidationError):
            RenderClipRequest(
                start=0,
                end=5,
                crop_keyframes=[
                    {"time": 3, "x": 0.2, "y": 0.4},
                    {"time": 2, "x": 0.8, "y": 0.6},
                ],
            )
        with self.assertRaises(ValidationError):
            RenderClipRequest(
                start=0,
                end=5,
                crop_keyframes=[{"time": 6, "x": 0.5, "y": 0.5}],
            )


if __name__ == "__main__":
    unittest.main()
