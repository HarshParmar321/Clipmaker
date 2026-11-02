"""The source resolution is the ceiling on everything downstream.

A real job downloaded a 640x360 source while the cap was set to 1440p, and
nothing in the logs said so. The cause was that download_video accepted the
first client strategy that worked at all — even one offering only the 360p
muxed stream — instead of comparing what each client could actually provide.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import downloader  # noqa: E402
from services.exceptions import VideoDownloadError  # noqa: E402


def _info(*heights: int, duration: int = 120) -> dict:
    return {
        "duration": duration,
        "formats": [{"vcodec": "avc1", "height": height} for height in heights],
    }


class BestAvailableHeightTests(unittest.TestCase):
    def test_ignores_audio_only_and_storyboard_formats(self):
        info = {
            "formats": [
                {"vcodec": "none", "height": None},
                {"vcodec": None, "height": 0},
                {"vcodec": "avc1", "height": 720},
            ]
        }
        self.assertEqual(downloader._best_available_height(info), 720)

    def test_no_real_video_reads_as_zero(self):
        self.assertEqual(
            downloader._best_available_height({"formats": [{"vcodec": "none"}]}), 0
        )


class ClientSelectionTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(downloader, "CLIENT_STRATEGIES", ["low", "high"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, probes, tmp: Path, download_side_effect=None):
        (tmp / "original.mp4").write_bytes(b"x")
        with patch.object(downloader, "_probe_strategy", side_effect=probes) as probe, \
             patch.object(downloader, "yt_dlp") as ytdlp, \
             patch.object(downloader, "_clear_partials", lambda _dir: None):
            handle = MagicMock()
            if download_side_effect is not None:
                handle.download.side_effect = download_side_effect
            ytdlp.YoutubeDL.return_value.__enter__.return_value = handle
            downloader.download_video("https://example.test/v", "job", tmp)
        return probe, ytdlp

    def test_a_low_resolution_client_does_not_win_over_a_better_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            probe, ytdlp = self._run([_info(360), _info(1080)], tmp)
            self.assertEqual(probe.call_count, 2, "stopped probing at the 360p client")
            chosen = ytdlp.YoutubeDL.call_args.args[0]
            self.assertEqual(
                chosen["extractor_args"]["youtube"]["player_client"], ["high"]
            )

    def test_stops_probing_once_a_good_enough_source_is_found(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            probe, _ = self._run([_info(1080), _info(1440)], tmp)
            self.assertEqual(probe.call_count, 1, "kept probing after a 1080p hit")

    def test_downloads_exactly_once(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, ytdlp = self._run([_info(360), _info(1080)], tmp)
            downloads = ytdlp.YoutubeDL.return_value.__enter__.return_value.download
            self.assertEqual(downloads.call_count, 1)

    def test_format_selector_honours_the_source_cap(self):
        from config import MAX_SOURCE_HEIGHT
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            selector = downloader._build_base_opts(Path(raw))["format"]
        self.assertIn(f"bestvideo[height<={MAX_SOURCE_HEIGHT}]", selector)
        self.assertIn("bestaudio", selector)

class JsRuntimeTests(unittest.TestCase):
    """Without a JS runtime, YouTube's high-resolution formats are unreachable.

    yt-dlp enables only "deno" by default, so a machine with Node but no Deno
    silently downloads 360p. That is what produced a 640x360 source on a job
    configured for 1440p.
    """

    def test_every_runtime_is_offered_to_ytdlp(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            opts = downloader._build_base_opts(Path(raw))
        self.assertIn("js_runtimes", opts)
        for runtime in ("deno", "node"):
            self.assertIn(runtime, opts["js_runtimes"])
        # Must be a dict of {runtime: config}; a list raises in YoutubeDL.
        self.assertIsInstance(opts["js_runtimes"], dict)

    def test_runtimes_are_configurable(self):
        import tempfile
        with patch.object(downloader, "JS_RUNTIMES", ["node"]):
            with tempfile.TemporaryDirectory() as raw:
                opts = downloader._build_base_opts(Path(raw))
        self.assertEqual(opts["js_runtimes"], {"node": {}})

    def test_options_are_accepted_by_ytdlp_itself(self):
        """The contract test. Unit-testing the shape we *think* is right is
        how a list slipped through and broke every download."""
        import tempfile
        import yt_dlp as real_ytdlp
        with tempfile.TemporaryDirectory() as raw:
            opts = downloader._build_base_opts(Path(raw))
        opts["quiet"] = True
        real_ytdlp.YoutubeDL(opts)  # raises if any option is malformed

    def test_startup_check_reports_a_missing_runtime(self):
        import startup_checks
        with patch.object(downloader, "JS_RUNTIMES", ["definitely-not-installed"]):
            self.assertFalse(startup_checks.check_js_runtime())

    def test_startup_check_passes_when_one_is_present(self):
        import startup_checks
        with patch.object(downloader, "JS_RUNTIMES", ["python3"]):
            self.assertTrue(startup_checks.check_js_runtime())


class DownloadFallbackTests(unittest.TestCase):
    """A 403 mid-transfer is not the same as a video we cannot have.

    YouTube signs a URL and then rejects it a few percent into a large file.
    The client that was handed that URL is unusable, but another one may work,
    so the job should move on instead of failing.
    """

    def setUp(self):
        patcher = patch.object(downloader, "CLIENT_STRATEGIES", ["first", "second"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_403_falls_through_to_the_next_client(self):
        import tempfile
        forbidden = RuntimeError("unable to download video data: HTTP Error 403: Forbidden")
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            (tmp / "original.mp4").write_bytes(b"x")
            with patch.object(downloader, "_probe_strategy",
                              side_effect=[_info(1080), _info(720)]), \
                 patch.object(downloader, "yt_dlp") as ytdlp, \
                 patch.object(downloader, "_clear_partials", lambda _dir: None):
                handle = MagicMock()
                handle.download.side_effect = [forbidden, None]
                ytdlp.YoutubeDL.return_value.__enter__.return_value = handle
                downloader.download_video("https://example.test/v", "job", tmp)
            self.assertEqual(handle.download.call_count, 2)

    def test_gives_up_only_after_every_client(self):
        import tempfile
        forbidden = RuntimeError("HTTP Error 403: Forbidden")
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            with patch.object(downloader, "_probe_strategy",
                              side_effect=[_info(1080), _info(720)]), \
                 patch.object(downloader, "yt_dlp") as ytdlp, \
                 patch.object(downloader, "_clear_partials", lambda _dir: None):
                handle = MagicMock()
                handle.download.side_effect = forbidden
                ytdlp.YoutubeDL.return_value.__enter__.return_value = handle
                with self.assertRaises(VideoDownloadError) as raised:
                    downloader.download_video("https://example.test/v", "job", tmp)
            self.assertEqual(handle.download.call_count, 2)
            self.assertIn("refused the download", raised.exception.user_message)


class FailureMessageTests(unittest.TestCase):
    def test_a_403_is_named_as_a_youtube_refusal(self):
        message = downloader._download_failure_message(
            RuntimeError("HTTP Error 403: Forbidden")
        )
        self.assertIn("refused the download", message)

    def test_a_bad_option_is_named_as_our_bug(self):
        """A malformed yt-dlp option once produced 'YouTube may be blocking',
        which sent debugging in exactly the wrong direction."""
        message = downloader._download_failure_message(
            ValueError("Invalid js_runtimes format")
        )
        self.assertIn("misconfigured", message)
        self.assertNotIn("YouTube may be", message)

    def test_anything_else_keeps_the_general_advice(self):
        """Now platform-neutral: the same generic path serves Twitch and Vimeo
        as well as YouTube, so it can no longer name one of them."""
        message = downloader._download_failure_message(RuntimeError("who knows"))
        self.assertIn("blocking automated downloads", message)
        self.assertIn("upload the file directly", message)


class ChunkedDownloadTests(unittest.TestCase):
    def test_ranged_requests_are_enabled(self):
        """A single long GET is what YouTube 403s partway through."""
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            opts = downloader._build_base_opts(Path(raw))
        self.assertEqual(opts["http_chunk_size"], downloader.HTTP_CHUNK_SIZE)
        self.assertGreater(opts["fragment_retries"], 1)


class TransientRetryTests(unittest.TestCase):
    """'The page needs to be reloaded' is YouTube stalling, not refusing.

    It cost us the tv client on a run where tv was the best option left, and
    the very next request would have succeeded.
    """

    def test_a_transient_probe_error_is_retried_once(self):
        import tempfile
        transient = RuntimeError("ERROR: [youtube] abc: The page needs to be reloaded.")
        with tempfile.TemporaryDirectory() as raw:
            with patch.object(downloader, "_probe_strategy",
                              side_effect=[transient, _info(1080)]) as probe, \
                 patch.object(downloader.time, "sleep", lambda _s: None):
                info = downloader._probe_strategy_with_retry(
                    "https://example.test/v", Path(raw), "tv", "job"
                )
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(downloader._best_available_height(info), 1080)

    def test_a_real_failure_is_not_retried(self):
        import tempfile
        permanent = RuntimeError("Requested format is not available")
        with tempfile.TemporaryDirectory() as raw:
            with patch.object(downloader, "_probe_strategy",
                              side_effect=permanent) as probe:
                with self.assertRaises(RuntimeError):
                    downloader._probe_strategy_with_retry(
                        "https://example.test/v", Path(raw), "ios", "job"
                    )
        self.assertEqual(probe.call_count, 1)


class ClientLadderTests(unittest.TestCase):
    def test_token_free_clients_are_tried_before_the_crippled_ones(self):
        """android tops out at 360p, so it must never be reached before the
        clients that can still serve 1080p without a PO token."""
        order = downloader.CLIENT_STRATEGIES
        for token_free in ("android_vr", "web_embedded", "tv"):
            self.assertIn(token_free, order)
            self.assertLess(order.index(token_free), order.index("android"))

    def test_default_is_still_first(self):
        self.assertEqual(downloader.CLIENT_STRATEGIES[0], "default")

    def test_format_selector_has_an_unfiltered_last_resort(self):
        """Without it, a client exposing only HLS fails outright."""
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            selector = downloader._build_base_opts(Path(raw))["format"]
        self.assertTrue(selector.endswith("/bestvideo+bestaudio/best"))


if __name__ == "__main__":
    unittest.main()
