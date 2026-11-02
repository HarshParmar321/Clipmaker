"""Regression tests for how many clips actually come back.

A user asking for five clips and receiving one is the failure these cover.
Two things caused it: a hard `score < 60` floor inside validate_highlights
(the same filter that had already been removed from the prompt), and a fixed
0.62 topic-similarity ceiling that treats every candidate in a single-subject
video as a duplicate of the first.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import highlight_detector  # noqa: E402
from services.face_tracker import source_zoom_ceiling  # noqa: E402


def _transcript(seconds: float = 320.0) -> list[dict]:
    words = []
    time = 0.0
    index = 0
    while time < seconds:
        text = "cooking" if index % 3 else "food"
        if index % 25 == 24:
            text += "."
        words.append({"word": text, "start": time, "end": time + 0.4})
        time += 0.5
        index += 1
    return words


def _candidates(scores: int, starts=(5, 40, 80, 120, 160), title="Cooking tips"):
    return [
        {
            "start": start,
            "end": start + 25,
            "title": title,
            "reason": "x",
            "hook_score": scores,
            "relevance_score": scores,
            "completeness_score": scores,
            "shareability_score": scores,
        }
        for start in starts
    ]


class ClipSelectionTests(unittest.TestCase):
    def test_single_topic_video_returns_more_than_one_clip(self):
        """Every candidate shares a topic. The old 0.62 ceiling kept one."""
        clips = highlight_detector.validate_highlights(
            _candidates(78),
            320,
            _transcript(),
            target_clip_count=5,
            min_clip_duration=20,
            max_clip_duration=60,
        )
        self.assertGreater(len(clips), 1, "single-topic video collapsed to one clip")

    def test_mediocre_candidates_are_still_returned(self):
        """Scoring ranks; it must not silently drop everything below 60."""
        clips = highlight_detector.validate_highlights(
            _candidates(48),
            320,
            _transcript(),
            target_clip_count=5,
            min_clip_duration=20,
            max_clip_duration=60,
        )
        self.assertGreater(len(clips), 0, "a hard score floor is filtering again")

    def test_best_candidate_still_wins(self):
        """Relaxing the floor must not reorder the premium pick."""
        raw = _candidates(48)
        raw[2] = {**raw[2], "hook_score": 92, "relevance_score": 90,
                  "completeness_score": 90, "shareability_score": 88}
        clips = highlight_detector.validate_highlights(
            raw, 320, _transcript(), target_clip_count=1,
            min_clip_duration=20, max_clip_duration=60,
        )
        self.assertEqual(len(clips), 1)
        self.assertGreater(clips[0]["score"], 80)

    def test_selection_never_exceeds_target(self):
        clips = highlight_detector.validate_highlights(
            _candidates(78), 320, _transcript(), target_clip_count=2,
            min_clip_duration=20, max_clip_duration=60,
        )
        self.assertLessEqual(len(clips), 2)

    def test_clips_are_returned_in_timeline_order(self):
        clips = highlight_detector.validate_highlights(
            _candidates(78), 320, _transcript(), target_clip_count=5,
            min_clip_duration=20, max_clip_duration=60,
        )
        self.assertEqual([c["start"] for c in clips],
                         sorted(c["start"] for c in clips))

    def test_no_clips_overlap(self):
        clips = highlight_detector.validate_highlights(
            _candidates(78), 320, _transcript(), target_clip_count=5,
            min_clip_duration=20, max_clip_duration=60,
        )
        for earlier, later in zip(clips, clips[1:]):
            self.assertLessEqual(earlier["end"], later["start"])


class ZoomCeilingTests(unittest.TestCase):
    def test_low_resolution_source_gets_no_zoom(self):
        """720p cannot fill a 1080-wide vertical frame at any zoom."""
        self.assertEqual(source_zoom_ceiling(720), 1.0)

    def test_ceiling_rises_with_source_resolution(self):
        self.assertLess(source_zoom_ceiling(1080), source_zoom_ceiling(1440))
        self.assertLess(source_zoom_ceiling(1440), source_zoom_ceiling(2160))

    def test_ceiling_is_bounded(self):
        self.assertLessEqual(source_zoom_ceiling(4320), 3.0)
        self.assertGreaterEqual(source_zoom_ceiling(240), 1.0)


if __name__ == "__main__":
    unittest.main()
