"""Clipmaker accepts video from many platforms, not just YouTube.

The API used to hard-reject every host except youtube.com, and the downloader's
whole client ladder was YouTube-specific machinery. These cover the boundary:
what the API lets in, and that platform-specific handling stays where it
belongs.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import downloader  # noqa: E402
from services.platforms import (  # noqa: E402
    PLATFORMS,
    find_platform,
    normalise_host,
    platform_for,
    supported_names,
)


class HostMatchingTests(unittest.TestCase):
    def test_common_links_resolve_to_their_platform(self):
        cases = {
            "https://www.youtube.com/watch?v=abc": "YouTube",
            "https://youtu.be/abc": "YouTube",
            "https://m.youtube.com/watch?v=abc": "YouTube",
            "https://music.youtube.com/watch?v=abc": "YouTube",
            "https://www.twitch.tv/videos/123": "Twitch",
            "https://clips.twitch.tv/SomeClip": "Twitch",
            "https://vimeo.com/123": "Vimeo",
            "https://player.vimeo.com/video/123": "Vimeo",
            "https://www.loom.com/share/abc": "Loom",
            "https://x.com/user/status/1": "X (Twitter)",
            "https://twitter.com/user/status/1": "X (Twitter)",
            "https://old.reddit.com/r/videos/comments/1": "Reddit",
            "https://v.redd.it/abc": "Reddit",
            "https://www.instagram.com/reel/abc/": "Instagram",
            "https://vm.tiktok.com/abc": "TikTok",
            "https://fb.watch/abc": "Facebook",
            "https://dai.ly/abc": "Dailymotion",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                found = find_platform(url)
                self.assertIsNotNone(found, f"{url} matched nothing")
                self.assertEqual(found.name, expected)

    def test_unknown_hosts_are_not_matched(self):
        for url in ("https://example.com/v", "https://notyoutube.com/watch?v=1", "not a url"):
            with self.subTest(url=url):
                self.assertIsNone(find_platform(url))

    def test_a_lookalike_domain_does_not_match(self):
        """endswith() on a bare domain would let evilyoutube.com through."""
        self.assertIsNone(find_platform("https://evilyoutube.com/watch?v=1"))

    def test_www_is_stripped_but_nothing_else_is_invented(self):
        self.assertEqual(normalise_host("WWW.Twitch.TV"), "twitch.tv")
        self.assertEqual(normalise_host("clips.twitch.tv"), "clips.twitch.tv")

    def test_every_platform_is_reachable_by_its_own_hosts(self):
        for platform in PLATFORMS:
            for host in platform.hosts:
                with self.subTest(platform=platform.key, host=host):
                    self.assertEqual(find_platform(f"https://{host}/x"), platform)

    def test_supported_names_are_user_facing(self):
        names = supported_names()
        self.assertIn("YouTube", names)
        self.assertIn("Twitch", names)
        self.assertEqual(len(names), len(PLATFORMS))


class StrategyDispatchTests(unittest.TestCase):
    def test_only_youtube_gets_a_client_ladder(self):
        """player_client is a YouTube extractor argument; it means nothing
        to Twitch or Vimeo and must not be sent to them."""
        import tempfile
        twitch = find_platform("https://twitch.tv/videos/1")
        with tempfile.TemporaryDirectory() as raw:
            opts = downloader._client_opts(Path(raw), "android_vr", twitch)
        self.assertNotIn("extractor_args", opts)

    def test_youtube_does_get_one(self):
        import tempfile
        youtube = find_platform("https://youtube.com/watch?v=1")
        with tempfile.TemporaryDirectory() as raw:
            opts = downloader._client_opts(Path(raw), "android_vr", youtube)
        self.assertEqual(
            opts["extractor_args"]["youtube"]["player_client"], ["android_vr"]
        )

    def test_non_youtube_platforms_make_a_single_pass(self):
        for platform in PLATFORMS:
            if platform.key == "youtube":
                continue
            with self.subTest(platform=platform.key):
                self.assertEqual(platform.client_strategies, ())

    def test_an_unknown_url_still_resolves_to_something(self):
        """The API rejects unsupported hosts; the downloader must not crash."""
        self.assertIsNotNone(platform_for("https://example.com/v"))


class CookieTests(unittest.TestCase):
    def test_a_platform_can_have_its_own_cookies(self):
        twitch = find_platform("https://twitch.tv/videos/1")
        with patch.dict("os.environ", {"YTDLP_COOKIES_FILE_TWITCH": "/tmp/twitch.txt"}):
            self.assertEqual(twitch.cookies_file(), "/tmp/twitch.txt")

    def test_it_falls_back_to_the_shared_cookies_file(self):
        vimeo = find_platform("https://vimeo.com/1")
        with patch.dict("os.environ", {"YTDLP_COOKIES_FILE": "/tmp/shared.txt"}, clear=False):
            self.assertEqual(vimeo.cookies_file(), "/tmp/shared.txt")

    def test_youtube_keeps_using_the_original_variable(self):
        """Backwards compatibility: the existing cookies.txt setup must keep
        working without anyone editing .env."""
        youtube = find_platform("https://youtube.com/watch?v=1")
        self.assertEqual(youtube.cookies_env, "YTDLP_COOKIES_FILE")


class FailureMessageTests(unittest.TestCase):
    def test_the_message_names_the_platform_the_user_actually_used(self):
        twitch = find_platform("https://twitch.tv/videos/1")
        message = downloader._download_failure_message(RuntimeError("boom"), twitch)
        self.assertIn("Twitch", message)
        self.assertNotIn("YouTube", message)

    def test_login_heavy_platforms_say_so(self):
        instagram = find_platform("https://instagram.com/reel/x/")
        message = downloader._download_failure_message(RuntimeError("boom"), instagram)
        self.assertIn("sign-in cookies", message)

    def test_our_own_bug_is_never_blamed_on_a_platform(self):
        twitch = find_platform("https://twitch.tv/videos/1")
        message = downloader._download_failure_message(
            ValueError("Invalid js_runtimes format"), twitch
        )
        self.assertIn("misconfigured", message)
        self.assertNotIn("Twitch", message)

    def test_every_message_points_at_upload_as_the_way_out(self):
        for platform in PLATFORMS:
            with self.subTest(platform=platform.key):
                message = downloader._download_failure_message(
                    RuntimeError("boom"), platform
                )
                self.assertIn("upload", message.lower())


class ApiValidationTests(unittest.TestCase):
    """The API used to reject every host but youtube.com outright."""

    def _payload(self, url: str):
        from routers.jobs import CreateJobFromUrl
        return CreateJobFromUrl(url=url)

    def test_supported_platforms_are_accepted(self):
        for url in (
            "https://www.youtube.com/watch?v=abc",
            "https://www.twitch.tv/videos/123",
            "https://vimeo.com/123",
            "https://x.com/user/status/1",
            "https://www.instagram.com/reel/abc/",
        ):
            with self.subTest(url=url):
                self.assertIsNotNone(self._payload(url))

    def test_an_unsupported_host_is_rejected_helpfully(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError) as raised:
            self._payload("https://example.com/video.mp4")
        message = str(raised.exception)
        self.assertIn("YouTube", message, "the error should list what IS supported")
        self.assertIn("upload", message.lower(), "and point at the path that always works")


if __name__ == "__main__":
    unittest.main()
