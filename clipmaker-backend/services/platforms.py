"""Which video platforms Clipmaker accepts, and how each one has to be fetched.

Clipmaker is not a YouTube tool — YouTube is just where most people's links come
from. Everything platform-specific lives here so the rest of the pipeline never
has to care where a video came from.

This is a tested allowlist rather than "anything yt-dlp supports". yt-dlp
handles well over a thousand sites, but many need a login or are region-locked,
and a user pasting a link to one of those gets a confusing failure on a site
nobody ever tried. An allowlist means every accepted URL is one we have a
reason to believe works, and adding a platform is one entry.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass(frozen=True)
class Platform:
    key: str
    name: str
    hosts: tuple[str, ...]
    # YouTube is the only platform that needs a client ladder: its player
    # clients are shown different format catalogues and different anti-bot
    # gates. Everywhere else, one generic pass is what yt-dlp expects, and
    # sending it a player_client would be meaningless.
    client_strategies: tuple[str, ...] = ()
    login_usually_required: bool = False
    note: str = ""
    _cookie_env: str = field(default="", repr=False)

    @property
    def cookies_env(self) -> str:
        return self._cookie_env or f"YTDLP_COOKIES_FILE_{self.key.upper()}"

    def cookies_file(self) -> str:
        """A per-platform cookies file, falling back to the global one.

        One cookies.txt was a YouTube-shaped assumption. Twitch subscriber VODs,
        private Vimeo, Instagram and Facebook each need their own session.
        """
        specific = os.getenv(self.cookies_env, "").strip()
        if specific:
            return specific
        return os.getenv("YTDLP_COOKIES_FILE", "").strip()


YOUTUBE_CLIENT_STRATEGIES = (
    "default",
    "android_vr",
    "web_embedded",
    "tv",
    "tv_embedded",
    "android",
    "ios",
)

PLATFORMS: tuple[Platform, ...] = (
    Platform(
        key="youtube",
        name="YouTube",
        hosts=("youtube.com", "youtu.be", "youtube-nocookie.com"),
        client_strategies=YOUTUBE_CLIENT_STRATEGIES,
        _cookie_env="YTDLP_COOKIES_FILE",
    ),
    Platform(
        key="twitch",
        name="Twitch",
        hosts=("twitch.tv",),
        note="VODs and clips. Subscriber-only VODs need cookies.",
    ),
    Platform(key="vimeo", name="Vimeo", hosts=("vimeo.com",)),
    Platform(key="loom", name="Loom", hosts=("loom.com",)),
    Platform(
        key="x",
        name="X (Twitter)",
        hosts=("x.com", "twitter.com", "t.co"),
    ),
    Platform(key="reddit", name="Reddit", hosts=("reddit.com", "redd.it")),
    Platform(
        key="instagram",
        name="Instagram",
        hosts=("instagram.com",),
        login_usually_required=True,
        note="Blocks automation aggressively. Expect to need cookies.",
    ),
    Platform(
        key="tiktok",
        name="TikTok",
        hosts=("tiktok.com",),
        login_usually_required=True,
        note="Blocks automation aggressively. Expect to need cookies.",
    ),
    Platform(
        key="facebook",
        name="Facebook",
        hosts=("facebook.com", "fb.watch"),
        login_usually_required=True,
    ),
    Platform(key="dailymotion", name="Dailymotion", hosts=("dailymotion.com", "dai.ly")),
)

_BY_KEY = {platform.key: platform for platform in PLATFORMS}


def normalise_host(hostname: str) -> str:
    """Strip the subdomains that only ever mean 'the same site'.

    m.youtube.com, www.twitch.tv, clips.twitch.tv, vm.tiktok.com and old.reddit
    .com are all the platform they look like, so matching on a suffix beats
    listing every subdomain anyone might paste.
    """
    return (hostname or "").lower().strip().removeprefix("www.")


def find_platform(url: str) -> Platform | None:
    host = normalise_host(urlparse(str(url)).hostname or "")
    if not host:
        return None
    for platform in PLATFORMS:
        for candidate in platform.hosts:
            if host == candidate or host.endswith("." + candidate):
                return platform
    return None


def platform_for(url: str) -> Platform:
    """The platform for a URL, or YouTube-shaped defaults for an unknown host.

    Only the API layer rejects unsupported hosts. The downloader stays tolerant
    so an internal caller (a re-run, a test, a future platform) never crashes on
    a lookup.
    """
    return find_platform(url) or _BY_KEY["youtube"]


def supported_names() -> list[str]:
    return [platform.name for platform in PLATFORMS]
