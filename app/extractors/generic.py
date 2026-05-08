import json
import logging
import re
from html.parser import HTMLParser

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class _GenericHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.media_sources = []
        self.video_sources = []
        self.audio_sources = []
        self.meta = {}
        self.json_ld = []
        self._in_json_ld = False

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == "script":
            script_type = str(attr_dict.get("type") or "").lower()
            self._in_json_ld = script_type == "application/ld+json"
        if tag == "video" and attr_dict.get("src"):
            self.video_sources.append(attr_dict.get("src"))
        if tag == "source" and attr_dict.get("src"):
            self.video_sources.append(attr_dict.get("src"))
        if tag == "audio" and attr_dict.get("src"):
            self.audio_sources.append(attr_dict.get("src"))
        if tag == "meta":
            prop = attr_dict.get("property") or attr_dict.get("name")
            if prop and attr_dict.get("content"):
                self.meta[prop] = attr_dict.get("content")
        if tag in {"a", "img", "source", "video", "audio"}:
            src = attr_dict.get("src") or attr_dict.get("href")
            if src:
                self.media_sources.append(src)

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_json_ld = False

    def handle_data(self, data):
        if self._in_json_ld and data:
            payload = data.strip()
            if payload:
                self.json_ld.append(payload)
            return
        if self.lasttag == "script" and data:
            data = data.strip()
            if data.startswith("{") and "contentUrl" in data:
                self.json_ld.append(data)


class GenericExtractor(BaseExtractor):
    PLATFORM_ID = "generic"
    REQUIRES_HEADLESS = False
    REQUIRES_PROXY = False
    TEST_URL = "https://example.com"

    def extract(self, url: str) -> dict:
        response = self.http_get(url)
        html = response.text

        parser = _GenericHTMLParser()
        parser.feed(html)

        title = parser.meta.get("og:title")
        description = parser.meta.get("og:description")
        thumbnail = parser.meta.get("og:image")
        author = parser.meta.get("og:site_name")

        media_url = None
        is_hls = False
        manifest_url = None

        candidates = []
        seen = set()

        def add_candidate(value):
            if not value or not isinstance(value, str):
                return
            candidate = value.strip()
            if not candidate or not candidate.startswith("http"):
                return
            if candidate in seen:
                return
            seen.add(candidate)
            candidates.append(candidate)

        def collect_json_ld_urls(payload):
            if isinstance(payload, dict):
                for key in ("contentUrl", "embedUrl", "thumbnailUrl"):
                    add_candidate(payload.get(key))
                collect_json_ld_urls(payload.get("image"))
                collect_json_ld_urls(payload.get("video"))
                collect_json_ld_urls(payload.get("audio"))
                collect_json_ld_urls(payload.get("mainEntity"))
                collect_json_ld_urls(payload.get("@graph"))
            elif isinstance(payload, list):
                for item in payload:
                    collect_json_ld_urls(item)
            elif isinstance(payload, str):
                add_candidate(payload)

        for src in parser.video_sources:
            add_candidate(src)
        for src in parser.audio_sources:
            add_candidate(src)

        for key in ("og:video", "og:video:secure_url", "twitter:player:stream"):
            add_candidate(parser.meta.get(key))

        for block in parser.json_ld:
            try:
                payload = json.loads(block)
            except json.JSONDecodeError:
                continue
            collect_json_ld_urls(payload)

        match = re.search(r"https?://[^\s\"']+\.m3u8[^\s\"']*", html)
        if match:
            manifest_url = match.group(0)
            add_candidate(manifest_url)

        match = re.search(r"https?://[^\s\"']+\.mpd[^\s\"']*", html)
        if match:
            add_candidate(match.group(0))

        for src in parser.media_sources:
            if re.search(r"\.(mp4|webm|m4v|mov)(?:\?|\Z)", src, re.IGNORECASE):
                add_candidate(src)

        for key in (
            "og:image",
            "og:image:secure_url",
            "twitter:image",
            "twitter:image:src",
            "twitter:image:secure_url",
        ):
            add_candidate(parser.meta.get(key))

        for src in parser.media_sources:
            if re.search(r"\.(jpg|jpeg|png|gif|webp)(?:\?|\Z)", src, re.IGNORECASE):
                add_candidate(src)

        if candidates:
            media_url = candidates[0]

        if media_url and ".m3u8" in media_url.lower():
            is_hls = True
            if not manifest_url:
                manifest_url = media_url

        if not media_url:
            raise ExtractorError(
                "No downloadable media found at this URL. The content may be dynamically loaded or require login.",
                platform=self.PLATFORM_ID,
                url=url,
            )

        qualities = [
            {
                "label": "original",
                "url": media_url,
                "size_bytes": None,
                "codec": None,
                "bitrate": None,
                "hdr": False,
                "format": self._guess_format(media_url),
            }
        ]

        return {
            "title": title or "Media",
            "author": author,
            "channel_id": None,
            "thumbnail": thumbnail,
            "duration": None,
            "view_count": None,
            "description": description,
            "upload_date": None,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": is_hls,
            "manifest_url": manifest_url,
            "headers_required": {},
        }

    def _guess_format(self, url: str) -> str:
        match = re.search(r"\.([a-zA-Z0-9]+)(?:\?|\Z)", url)
        if match:
            return match.group(1).lower()
        return "mp4"
