"""Highlight detection used to be one prompt for the whole recording.

A three-hour podcast is roughly 27,000 words. Asked to find five moments in all
of it at once, a model either refuses the context or skims and returns
something generic. The transcript is now split into windows the model actually
reads, and the existing global ranking pass reduces the combined candidates.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import highlight_detector as detector  # noqa: E402
from services.exceptions import VideoDownloadError  # noqa: E402


def _words(count: int, start: float = 0.0) -> list[dict]:
    return [
        {"word": "word", "start": round(start + index * 0.4, 3),
         "end": round(start + index * 0.4 + 0.35, 3)}
        for index in range(count)
    ]


def _clip(start: float) -> dict:
    return {
        "title": f"Moment at {start:.0f}", "start": start, "end": start + 30,
        "reason": "x", "hook_score": 80, "relevance_score": 80,
        "completeness_score": 80, "shareability_score": 80,
    }


class WindowSplitTests(unittest.TestCase):
    def test_a_short_transcript_is_one_window(self):
        self.assertEqual(len(detector._window_word_lists(_words(500), window_words=2500)), 1)

    def test_a_long_transcript_is_split(self):
        windows = detector._window_word_lists(_words(27000), window_words=2500, overlap_words=200)
        self.assertGreater(len(windows), 10)

    def test_windows_overlap_so_a_moment_on_a_boundary_survives(self):
        windows = detector._window_word_lists(_words(6000), window_words=2500, overlap_words=200)
        first_end = windows[0][-1]["start"]
        second_start = windows[1][0]["start"]
        self.assertLess(second_start, first_end, "windows do not overlap")

    def test_the_whole_transcript_is_covered(self):
        words = _words(6000)
        windows = detector._window_word_lists(words, window_words=2500, overlap_words=200)
        self.assertEqual(windows[0][0], words[0])
        self.assertEqual(windows[-1][-1], words[-1])

    def test_splitting_terminates_even_with_absurd_overlap(self):
        """overlap >= window would make stride zero and loop forever."""
        windows = detector._window_word_lists(_words(5000), window_words=1000, overlap_words=99999)
        self.assertGreater(len(windows), 1)
        self.assertLess(len(windows), 200)

    def test_timestamps_stay_absolute_across_windows(self):
        """Candidates from window 12 must point at 3 hours in, not 15 minutes."""
        words = _words(6000)
        windows = detector._window_word_lists(words, window_words=2500, overlap_words=200)
        self.assertGreater(windows[-1][0]["start"], 1000)


class MapReduceTests(unittest.TestCase):
    LONG = _words(9000)

    def test_every_window_is_asked_and_all_candidates_come_back(self):
        seen = []

        def fake(words, **kwargs):
            seen.append(float(words[0]["start"]))
            return [_clip(float(words[0]["start"]) + 10)]

        with patch.object(detector, "_request_candidates", side_effect=fake):
            clips = detector.detect_highlights(self.LONG, target_clip_count=5)
        self.assertGreater(len(seen), 1, "did not window a 9000-word transcript")
        self.assertEqual(len(clips), len(seen))

    def test_one_failing_window_does_not_lose_the_others(self):
        """A three-hour job is expensive to retry from scratch."""
        calls = {"n": 0}

        def flaky(words, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise VideoDownloadError("model hiccup", technical_detail="429")
            return [_clip(float(words[0]["start"]) + 10)]

        with patch.object(detector, "_request_candidates", side_effect=flaky):
            clips = detector.detect_highlights(self.LONG, target_clip_count=5)
        self.assertGreaterEqual(len(clips), 1)
        self.assertEqual(len(clips), calls["n"] - 1)

    def test_it_only_fails_when_every_window_fails(self):
        with patch.object(detector, "_request_candidates",
                          side_effect=VideoDownloadError("dead", technical_detail="x")):
            with self.assertRaises(VideoDownloadError) as raised:
                detector.detect_highlights(self.LONG, target_clip_count=5)
        self.assertTrue(raised.exception.retryable)

    def test_progress_is_reported_per_window(self):
        seen = []
        with patch.object(detector, "_request_candidates", return_value=[_clip(10)]):
            detector.detect_highlights(
                self.LONG, target_clip_count=5, on_progress=lambda i, n: seen.append((i, n))
            )
        self.assertGreater(len(seen), 1)
        self.assertEqual([index for index, _ in seen], list(range(1, len(seen) + 1)))

    def test_each_window_asks_for_a_share_not_the_full_quota(self):
        """Twenty windows each returning 24 candidates is 480 candidates and a
        lot of wasted tokens."""
        asked = []

        def fake(words, *, candidate_count, **kwargs):
            asked.append(candidate_count)
            return [_clip(float(words[0]["start"]))]

        with patch.object(detector, "_request_candidates", side_effect=fake):
            detector.detect_highlights(self.LONG, target_clip_count=5)
        self.assertTrue(all(count < 24 for count in asked), f"asked for {asked}")
        self.assertTrue(all(count >= 3 for count in asked), "too few to be useful")

    def test_a_short_video_still_takes_the_single_request_path(self):
        calls = []
        with patch.object(detector, "_request_candidates",
                          side_effect=lambda w, **k: calls.append(1) or [_clip(5)]):
            detector.detect_highlights(_words(500), target_clip_count=5)
        self.assertEqual(len(calls), 1)

    def test_the_short_transcript_guard_still_applies(self):
        with self.assertRaises(VideoDownloadError):
            detector.detect_highlights(_words(10))

    def test_the_model_is_told_timestamps_are_absolute(self):
        """Without this the model renumbers each window from zero and every
        clip after the first points at the wrong part of the video."""
        captured = {}

        def capture(model, system_prompt, user_message):
            captured["message"] = user_message
            raise VideoDownloadError("stop here", technical_detail="x")

        with patch.object(detector, "_complete_highlights", side_effect=capture):
            with self.assertRaises(VideoDownloadError):
                detector.detect_highlights(self.LONG, target_clip_count=5)
        self.assertIn("absolute", captured["message"].lower())


class NoBoundaryFallbackTests(unittest.TestCase):
    """A recording with no punctuation and no pauses must still yield clips.

    Whisper on a shouted gameplay stream can return long unbroken runs. Both
    the strict validator and the recovery pass work from sentence ends and
    speech gaps, so with neither they returned nothing — and zero clips from a
    three-hour recording is the worst outcome the product can produce.
    """

    UNBROKEN = [
        {"word": "word", "start": round(i * 0.4, 3), "end": round(i * 0.4 + 0.35, 3)}
        for i in range(4000)
    ]

    def _raw(self):
        return [
            {"title": "x", "start": start, "end": start + 30, "reason": "r",
             "hook_score": 85, "relevance_score": 80,
             "completeness_score": 82, "shareability_score": 78}
            for start in (100, 400, 900)
        ]

    def test_time_slices_are_produced_when_nothing_else_can_be(self):
        clips = detector.recover_highlights(
            self._raw(), self.UNBROKEN[-1]["end"], self.UNBROKEN,
            target_clip_count=5, min_clip_duration=20, max_clip_duration=60,
        )
        self.assertEqual(len(clips), 5)

    def test_the_slices_respect_the_requested_duration_range(self):
        clips = detector.recover_highlights(
            self._raw(), self.UNBROKEN[-1]["end"], self.UNBROKEN,
            target_clip_count=5, min_clip_duration=20, max_clip_duration=60,
        )
        for clip in clips:
            length = clip["end"] - clip["start"]
            self.assertGreaterEqual(length, 20)
            self.assertLessEqual(length, 60)

    def test_they_do_not_overlap(self):
        clips = detector.recover_highlights(
            self._raw(), self.UNBROKEN[-1]["end"], self.UNBROKEN,
            target_clip_count=5, min_clip_duration=20, max_clip_duration=60,
        )
        for earlier, later in zip(clips, clips[1:]):
            self.assertLessEqual(earlier["end"], later["start"])

    def test_they_are_scored_low_enough_to_read_as_a_starting_point(self):
        """These are time slices, not judged moments. They must never be
        presented with the confidence of a real pick."""
        clips = detector.recover_highlights(
            self._raw(), self.UNBROKEN[-1]["end"], self.UNBROKEN,
            target_clip_count=5, min_clip_duration=20, max_clip_duration=60,
        )
        self.assertTrue(all(clip["score"] < 80 for clip in clips))

    def test_a_normal_transcript_never_reaches_the_fallback(self):
        """Punctuated speech must still snap to sentences, not time."""
        words = []
        for index in range(4000):
            text = "word." if index % 18 == 17 else "word"
            words.append({"word": text, "start": round(index * 0.4, 3),
                          "end": round(index * 0.4 + 0.35, 3)})
        clips = detector.recover_highlights(
            self._raw(), words[-1]["end"], words,
            target_clip_count=5, min_clip_duration=20, max_clip_duration=60,
        )
        starts = [clip["start"] for clip in clips]
        self.assertFalse(
            all(round(start) % 40 == 0 for start in starts),
            "snapped to a fixed grid, so it took the time-slice path",
        )


if __name__ == "__main__":
    unittest.main()
