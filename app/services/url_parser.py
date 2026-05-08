import logging
import re
from typing import Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx


logger = logging.getLogger(__name__)

_UTM_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
}

_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "ref",
    "igsh",
    "igshid",
    "s",
    "si",
    "feature",
    "app",
}

_SHORTENER_DOMAINS = {
    "bit.ly",
    "tinyurl.com",
    "pin.it",
    "t.co",
    "ow.ly",
    "goo.gl",
    "buff.ly",
    "dlvr.it",
    "ift.tt",
    "rb.gy",
    "is.gd",
    "v.gd",
    "b23.tv",
    "bili2233.cn",
}

PLATFORM_PATTERNS = {
    "youtube": [
        r"youtube\.com/watch",
        r"youtube\.com/shorts/",
        r"youtu\.be/",
        r"youtube\.com/playlist",
        r"youtube\.com/@[^/]+/videos",
        r"youtube\.com/live/",
        r"youtube\.com/embed/",
        r"music\.youtube\.com/",
    ],
    "tiktok": [
        r"tiktok\.com/@[^/]+/video/",
        r"vm\.tiktok\.com/",
        r"tiktok\.com/t/",
    ],
    "instagram": [
        r"instagram\.com/p/",
        r"instagram\.com/reel/",
        r"instagram\.com/stories/",
        r"instagram\.com/tv/",
        r"instagram\.com/reels/",
    ],
    "twitter": [
        r"twitter\.com/[^/]+/status/",
        r"x\.com/[^/]+/status/",
        r"mobile\.twitter\.com/[^/]+/status/",
    ],
    "reddit": [
        r"reddit\.com/r/[^/]+/comments/",
        r"redd\.it/",
        r"old\.reddit\.com/r/[^/]+/comments/",
        r"www\.reddit\.com/r/[^/]+/comments/",
    ],
    "twitch": [
        r"twitch\.tv/videos/",
        r"twitch\.tv/[^/]+/clip/",
        r"clips\.twitch\.tv/",
    ],
    "vimeo": [
        r"vimeo\.com/\d+",
        r"vimeo\.com/ondemand/[^/]+/\d+",
        r"vimeo\.com/channels/[^/]+/\d+",
        r"vimeo\.com/groups/[^/]+/videos/\d+",
        r"vimeo\.com/album/\d+/video/\d+",
        r"vimeo\.com/showcase/\d+/video/\d+",
        r"player\.vimeo\.com/video/",
    ],
    "soundcloud": [
        r"soundcloud\.com/[^/]+/[^/]+",
        r"soundcloud\.com/[^/]+/sets/",
    ],
    "bilibili": [
        r"bilibili\.com/video/BV",
        r"bilibili\.com/video/av",
        r"b23\.tv/",
    ],
    "facebook": [
        r"facebook\.com/[^/]+/videos/",
        r"facebook\.com/watch",
        r"fb\.watch/",
        r"facebook\.com/reel/",
    ],
    "dailymotion": [
        r"dailymotion\.com/video/",
        r"dai\.ly/",
    ],
    "spotify": [
        r"open\.spotify\.com/episode/",
        r"open\.spotify\.com/show/",
        r"open\.spotify\.com/track/",
    ],
    "linkedin": [
        r"linkedin\.com/posts/",
        r"linkedin\.com/feed/update/",
        r"linkedin\.com/in/",
        r"linkedin\.com/company/",
        r"linkedin\.com/embed/feed/update/",
        r"linkedin\.com/events/",
    ],
    "pinterest": [
        r"pinterest\.com/pin/",
        r"pin\.it/",
    ],
    "podcast_rss": [
        r"rss\.com/podcasts/",
        r"feeds\.simplecast\.com/",
        r"anchor\.fm/[^/]+/episodes/",
        r"buzzsprout\.com/\d+/episodes/",
        r"podbean\.com/[^/]+/post/",
        r"transistor\.fm/[^/]+/episodes/",
        r"libsyn\.com/",
        r"feeds\.buzzsprout\.com/",
        r"[^/]+\.rss$",
        r"/feed\.xml",
        r"/rss\.xml",
        r"/podcast\.xml",
        r"/feed/podcast",
        r"/rss/feed",
    ],
    "netflix": [
        r"netflix\.com/watch/\d+",
        r"netflix\.com/title/\d+",
        r"netflix\.com/[a-z-]+/title/",
        r"netflix\.com/browse",
    ],
    "coursera": [
        r"coursera\.org/lecture/",
        r"coursera\.org/learn/[^/]+/lecture/",
        r"coursera\.org/learn/[^/]+/lecture/[^/]+/preview",
        r"coursera\.org/projects/[^/?#]+",
        r"coursera\.org/specializations/",
        r"coursera\.org/professional-certificates/",
    ],
    "behance": [
        r"behance\.net/gallery/\d+/",
        r"behance\.net/[^/]+/projects",
        r"behance\.net/gallery/\d+/[^/]+$",
    ],
    "imgur": [
        r"imgur\.com/a/[a-zA-Z0-9]+",
        r"imgur\.com/gallery/[a-zA-Z0-9]+",
        r"imgur\.com/[a-zA-Z0-9]{5,10}$",
        r"i\.imgur\.com/[a-zA-Z0-9]+\.(jpg|jpeg|png|gif|gifv|mp4|webm)",
        r"imgur\.com/[a-zA-Z0-9]+\.(jpg|jpeg|png|gif|gifv)",
    ],
    "generic": [
        r".+",
    ],
}


VALID_CONTENT_TYPES = {"video", "audio", "image", "document", "post", "unknown"}


def normalize_content_type(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in VALID_CONTENT_TYPES:
        return normalized
    return "post"


def normalize_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    parsed = urlparse(url)
    scheme = parsed.scheme
    if scheme == "http":
        scheme = "https"
    query_params = parse_qsl(parsed.query, keep_blank_values=False)
    filtered = [
        (key, value)
        for key, value in query_params
        if key not in _UTM_PARAMS and key not in _TRACKING_PARAMS
    ]
    query = urlencode(filtered, doseq=True)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    cleaned = urlunparse((scheme, parsed.netloc, path, "", query, ""))
    return cleaned


def unshorten(url: str, max_hops: int = 5) -> str:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in _SHORTENER_DOMAINS:
        return url
    current_url = url
    try:
        with httpx.Client(follow_redirects=False, timeout=10) as client:
            for _ in range(max_hops):
                response = client.get(current_url)
                if response.status_code not in (301, 302, 303, 307, 308):
                    return current_url
                location = response.headers.get("Location")
                if not location:
                    return current_url
                current_url = urljoin(current_url, location)
        return current_url
    except httpx.HTTPError:
        return url


def infer_content_type(platform_id: str, path: str) -> str:
    value = (path or "").lower()
    if value.endswith((".jpg", ".jpeg", ".png", ".gif", ".gifv", ".webp", ".bmp")):
        return "image"
    if value.endswith((".mp3", ".m4a", ".flac", ".wav", ".aac", ".ogg", ".opus")):
        return "audio"
    if value.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv")):
        return "document"
    if platform_id in {"youtube", "tiktok", "twitch", "vimeo", "dailymotion", "facebook"}:
        return "video"
    if platform_id == "linkedin":
        if "/in/" in value or "/company/" in value:
            return "post"
        return "video"
    if platform_id in {"soundcloud", "spotify"}:
        return "audio"
    if platform_id == "podcast_rss":
        return "audio"
    if platform_id in {"netflix", "coursera"}:
        return "video"
    if platform_id in {"behance", "imgur"}:
        return "image"
    if platform_id in {"instagram", "pinterest"}:
        return "video"
    if platform_id == "twitter":
        return "post"
    if platform_id == "reddit":
        if "i.redd.it" in value or value.endswith((".jpg", ".jpeg", ".png", ".gif", ".gifv", ".webp")):
            return "image"
        return "video"
    return "post"


def parse_url(raw_url: str) -> Tuple[str, str, str]:
    url = (raw_url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("Invalid URL provided")
    normalized = normalize_url(url)
    clean_url = unshorten(normalized)
    parsed = urlparse(clean_url)
    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    target = hostname + (parsed.path or "")
    if parsed.query:
        target = f"{target}?{parsed.query}"
    for platform_id, patterns in PLATFORM_PATTERNS.items():
        if platform_id == "generic":
            continue
        for pattern in patterns:
            if re.search(pattern, target, re.IGNORECASE):
                content_type = infer_content_type(platform_id, target)
                logger.debug("Detected platform %s for url %s", platform_id, clean_url)
                return platform_id, normalize_content_type(content_type), clean_url

    if hostname == "instagram.com":
        content_type = infer_content_type("instagram", target)
        logger.debug("Detected platform %s for url %s by hostname", "instagram", clean_url)
        return "instagram", normalize_content_type(content_type), clean_url

    if hostname == "linkedin.com":
        content_type = infer_content_type("linkedin", target)
        logger.debug("Detected platform %s for url %s by hostname", "linkedin", clean_url)
        return "linkedin", normalize_content_type(content_type), clean_url

    if _looks_like_rss_by_content_type(clean_url):
        content_type = infer_content_type("podcast_rss", target)
        logger.debug("Detected platform %s for url %s by content-type", "podcast_rss", clean_url)
        return "podcast_rss", normalize_content_type(content_type), clean_url

    logger.debug("Detected platform %s for url %s", "generic", clean_url)
    return "generic", "post", clean_url


def _looks_like_rss_by_content_type(url: str) -> bool:
    try:
        with httpx.Client(follow_redirects=True, timeout=8) as client:
            response = client.head(url)
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if any(marker in content_type for marker in ("rss", "xml", "atom", "feed")):
                return True
    except httpx.HTTPError:
        return False
    return False
