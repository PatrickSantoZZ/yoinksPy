from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Platform:
    key: str
    label: str


_PLATFORMS = [
    (("youtube.com", "youtu.be", "music.youtube.com"), Platform("youtube", "YouTube")),
    (("x.com", "twitter.com"), Platform("x", "X / Twitter")),
    (("instagram.com",), Platform("instagram", "Instagram")),
    (("threads.net", "threads.com"), Platform("threads", "Threads")),
    (("tiktok.com",), Platform("tiktok", "TikTok")),
    (("vimeo.com",), Platform("vimeo", "Vimeo")),
    (("twitch.tv",), Platform("twitch", "Twitch")),
    (("reddit.com",), Platform("reddit", "Reddit")),
    (("facebook.com", "fb.watch"), Platform("facebook", "Facebook")),
]


def _hostname(url: str):
    if not url or any(c.isspace() for c in url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    if not parts.scheme or not host:
        return None
    return parts.scheme.lower(), host.lower()


def detect_platform(url: str) -> Platform:
    parsed = _hostname(url)
    if not parsed:
        return Platform("unknown", "Unknown site")
    hostname = parsed[1]
    for hosts, platform in _PLATFORMS:
        if any(hostname == h or hostname.endswith(f".{h}") for h in hosts):
            return platform
    return Platform("generic", hostname)


def is_probably_url(text: str) -> bool:
    parsed = _hostname(text.strip())
    return bool(parsed) and parsed[0] in ("http", "https")
