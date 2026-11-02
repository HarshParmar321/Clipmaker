"""Transcription had a hard 52-minute ceiling.

Groq caps audio uploads at 25MB; at 64kbps mono that is about 52 minutes. A
podcast or a stream failed outright. Audio is now transcribed in windows, and
the whole difficulty is the seam: Whisper is worse at the first and last words
of a clip, so windows overlap for context — but a word transcribed twice would
appear twice in the captions.
"""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import transcriber  # noqa: E402
from services.transcriber import plan_windows  # noqa: E402


class WindowPlanTests(unittest.TestCase):
    def test_a_short_video_is_a_single_window(self):
        self.assertEqual(len(plan_windows(300, chunk_seconds=600)), 1)

    def test_a_short_video_has_no_overlap_to_trim(self):
        windows = plan_windows(300, chunk_seconds=600)
        self.assertEqual(windows[0], (0.0, 300, 0.0))

    def test_windows_cover_the_whole_timeline_with_no_hole(self):
        windows = plan_windows(3600, chunk_seconds=600, overlap_seconds=4)
        boundaries = [keep_from for _, _, keep_from in windows]
        self.assertEqual(boundaries[0], 0.0)
        for earlier, later in zip(boundaries, boundaries[1:]):
            self.assertEqual(later - earlier, 600)
        last_from, last_duration, _ = windows[-1]
        self.assertAlmostEqual(last_from + last_duration, 3600, places=3)

    def test_every_window_but_the_first_is_extracted_early_for_context(self):
        windows = plan_windows(1800, chunk_seconds=600, overlap_seconds=4)
        self.assertEqual(windows[0][0], 0.0, "the first window cannot start before zero")
        for extract_from, _, keep_from in windows[1:]:
            self.assertAlmostEqual(keep_from - extract_from, 4)

    def test_a_four_hour_stream_produces_a_sane_number_of_windows(self):
        windows = plan_windows(4 * 3600, chunk_seconds=600)
        self.assertEqual(len(windows), 24)

    def test_no_window_can_exceed_the_upload_limit_at_64kbps(self):
        """4.8MB per 10 minutes; the guard is that nothing plans a huge window."""
        for total in (600, 3600, 4 * 3600):
            for _, duration, _ in plan_windows(total):
                estimated_bytes = duration * 64_000 / 8
                self.assertLess(estimated_bytes, transcriber.GROQ_UPLOAD_LIMIT_BYTES)

    def test_an_empty_video_plans_nothing(self):
        self.assertEqual(plan_windows(0), [])

    def test_overlap_can_never_swallow_a_whole_window(self):
        windows = plan_windows(3600, chunk_seconds=60, overlap_seconds=9999)
        for extract_from, _, keep_from in windows[1:]:
            self.assertLessEqual(keep_from - extract_from, 30)


class SeamTests(unittest.TestCase):
    """The overlap is for Whisper's context, not to be stitched."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.audio = Path(self._tmp.name) / "audio.mp3"

    def _run(self, per_window_words):
        calls = []

        def fake_extract(video_path, audio_path, *, start=None, duration=None):
            audio_path.write_bytes(b"\0" * 1024)
            calls.append(start or 0.0)
            return audio_path

        def fake_transcribe(path):
            return per_window_words[len(calls) - 1]

        with patch.object(transcriber, "probe_audio_duration", return_value=1800), \
             patch.object(transcriber, "extract_audio", side_effect=fake_extract), \
             patch.object(transcriber, "_transcribe_file", side_effect=fake_transcribe), \
             patch.object(transcriber, "TRANSCRIBE_CHUNK_SECONDS", 600):
            return transcriber.transcribe(Path("video.mp4"), self.audio)

    def test_a_word_heard_by_two_windows_appears_once(self):
        # Window 2 is extracted from 596s, so its local 0-4s repeats what
        # window 1 already transcribed at 596-600s.
        result = self._run([
            [{"word": "boundary", "start": 597.0, "end": 597.5}],   # window 1, absolute
            [{"word": "boundary", "start": 1.0, "end": 1.5},        # repeat, local
             {"word": "after", "start": 10.0, "end": 10.4}],
            [],
        ])
        self.assertEqual([word["word"] for word in result], ["boundary", "after"])

    def test_kept_words_land_on_the_video_timeline_not_the_window_timeline(self):
        result = self._run([
            [],
            [{"word": "later", "start": 10.0, "end": 10.5}],  # local to a window from 596s
            [],
        ])
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["start"], 606.0)

    def test_words_come_back_in_timeline_order(self):
        result = self._run([
            [{"word": "one", "start": 5.0, "end": 5.4}],
            [{"word": "two", "start": 100.0, "end": 100.4}],
            [{"word": "three", "start": 100.0, "end": 100.4}],
        ])
        starts = [word["start"] for word in result]
        self.assertEqual(starts, sorted(starts))

    def test_progress_is_reported_per_window(self):
        seen = []
        with patch.object(transcriber, "probe_audio_duration", return_value=1800), \
             patch.object(transcriber, "extract_audio",
                          side_effect=lambda v, a, **k: (a.write_bytes(b"\0"), a)[1]), \
             patch.object(transcriber, "_transcribe_file", return_value=[]):
            transcriber.transcribe(Path("v.mp4"), self.audio, on_progress=lambda i, n: seen.append((i, n)))
        self.assertEqual(seen[0][1], seen[-1][1], "total window count changed mid-run")
        self.assertEqual([index for index, _ in seen], list(range(1, len(seen) + 1)))

    def test_window_files_are_cleaned_up_even_when_a_window_fails(self):
        """A four-hour video makes 24 temp files. Leaking them on failure fills
        the disk exactly when something is already going wrong."""
        made: list[Path] = []

        def fake_extract(video_path, audio_path, *, start=None, duration=None):
            audio_path.write_bytes(b"\0" * 16)
            made.append(audio_path)
            return audio_path

        with patch.object(transcriber, "probe_audio_duration", return_value=1800), \
             patch.object(transcriber, "extract_audio", side_effect=fake_extract), \
             patch.object(transcriber, "_transcribe_file", side_effect=RuntimeError("groq down")):
            with self.assertRaises(RuntimeError):
                transcriber.transcribe(Path("v.mp4"), self.audio)
        self.assertTrue(made, "no window was ever extracted")
        for path in made:
            self.assertFalse(path.exists(), f"left {path.name} behind")


class UploadLimitTests(unittest.TestCase):
    def test_an_oversized_window_fails_with_advice_not_a_groq_error(self):
        with TemporaryDirectory() as raw:
            path = Path(raw) / "big.mp3"
            path.write_bytes(b"\0" * 16)
            with patch.object(transcriber, "GROQ_UPLOAD_LIMIT_BYTES", 8):
                with self.assertRaises(ValueError) as raised:
                    transcriber._transcribe_file(path)
        self.assertIn("TRANSCRIBE_CHUNK_SECONDS", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
