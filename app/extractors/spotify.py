import json
import logging
import re
import xml.etree.ElementTree as ET
from html import unescape
from datetime import datetime

import httpx

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class SpotifyExtractor(BaseExtractor):
    PLATFORM_ID = "spotify"
    REQUIRES_HEADLESS = False
    REQUIRES_PROXY = False
    TEST_URL = "https://open.spotify.com/episode/4rOoJ6Egrf8K2IrywzwOMk"

    def extract(self, url: str) -> dict:
        if "/track/" in url:
            return self._extract_track(url)
        if "/show/" in url:
            return self._extract_show(url)
        if "/episode/" in url:
            return self._extract_episode(url)
        raise ExtractorError("Unsupported Spotify URL", platform=self.PLATFORM_ID, url=url)

    def _extract_track(self, url: str) -> dict:
        html = self._fetch_page(url)
        meta = self._extract_meta_tags(html)
        audio_url = self._extract_audio_url(html)
        if not audio_url:
            raise ExtractorError(
                "Spotify full tracks are DRM-protected. This track does not expose a public preview clip.",
                platform=self.PLATFORM_ID,
                url=url,
            )

        title = meta.get("og:title") or meta.get("twitter:title") or "Spotify Track Preview"
        description = meta.get("og:description") or meta.get("twitter:description")
        artist = meta.get("music:musician_description") or self._artist_from_description(description)
        thumbnail = meta.get("og:image") or meta.get("twitter:image")

        duration = self._to_int(meta.get("music:duration"))
        upload_date = self._normalize_date(meta.get("music:release_date"))

        return {
            "title": title,
            "author": artist,
            "channel_id": None,
            "thumbnail": thumbnail,
            "duration": duration,
            "view_count": None,
            "description": description,
            "upload_date": upload_date,
            "qualities": [
                {
                    "label": "track_preview",
                    "display_label": "track_preview",
                    "selector": "spotify_track_preview",
                    "format_id": None,
                    "url": audio_url,
                    "size_bytes": None,
                    "codec": "mp3",
                    "bitrate": None,
                    "hdr": False,
                    "format": "mp3",
                    "has_audio": True,
                }
            ],
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {"Referer": "https://open.spotify.com/"},
        }

    def _extract_episode(self, url: str) -> dict:
        html = self._fetch_page(url)
        json_ld = self._extract_json_ld(html)
        audio_url = self._extract_audio_url(html)
        if not audio_url:
            raise ExtractorError(
                "This Spotify episode may require a premium account or is not publicly accessible without login.",
                platform=self.PLATFORM_ID,
                url=url,
            )

        duration = self._parse_duration(json_ld.get("duration")) if json_ld else None
        upload_date = None
        if json_ld and json_ld.get("datePublished"):
            upload_date = json_ld.get("datePublished")[:10]

        return {
            "title": json_ld.get("name") if json_ld else "Spotify Episode",
            "author": (json_ld.get("partOfSeries") or {}).get("name") if json_ld else None,
            "channel_id": None,
            "thumbnail": (json_ld.get("image") if json_ld else None),
            "duration": duration,
            "view_count": None,
            "description": (json_ld.get("description") if json_ld else None),
            "upload_date": upload_date,
            "qualities": [
                {
                    "label": "podcast_audio",
                    "url": audio_url,
                    "size_bytes": None,
                    "codec": "mp3",
                    "bitrate": None,
                    "hdr": False,
                    "format": "mp3",
                }
            ],
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {},
        }

    def _extract_show(self, url: str) -> dict:
        html = self._fetch_page(url)
        rss_url = self._extract_rss_url(html)
        if not rss_url:
            raise ExtractorError(
                "This Spotify show does not expose a public RSS feed.",
                platform=self.PLATFORM_ID,
                url=url,
            )
        try:
            response = self.http_get(rss_url)
            root = ET.fromstring(response.text)
        except Exception as exc:
            raise ExtractorError("Failed to load RSS feed", platform=self.PLATFORM_ID, url=url) from exc

        first_item = root.find(".//item")
        if first_item is None:
            raise ExtractorError("No episodes found in RSS feed", platform=self.PLATFORM_ID, url=url)
        title = first_item.findtext("title")
        enclosure = first_item.find("enclosure")
        audio_url = enclosure.attrib.get("url") if enclosure is not None else None
        if not audio_url:
            raise ExtractorError("RSS feed missing audio", platform=self.PLATFORM_ID, url=url)

        return {
            "title": title or "Spotify Episode",
            "author": root.findtext(".//channel/title"),
            "channel_id": None,
            "thumbnail": None,
            "duration": None,
            "view_count": None,
            "description": first_item.findtext("description"),
            "upload_date": None,
            "qualities": [
                {
                    "label": "podcast_audio",
                    "url": audio_url,
                    "size_bytes": None,
                    "codec": "mp3",
                    "bitrate": None,
                    "hdr": False,
                    "format": "mp3",
                }
            ],
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {},
        }

    def _fetch_page(self, url: str) -> str:
        try:
            response = self.http_get(url)
            return response.text
        except Exception as exc:
            raise ExtractorError("Failed to load Spotify page", platform=self.PLATFORM_ID, url=url) from exc

    def _extract_json_ld(self, html: str) -> dict:
        match = re.search(r"<script type=\"application/ld\+json\">(.*?)</script>", html, re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}

    def _extract_audio_url(self, html: str) -> str:
        match = re.search(r'<meta\s+property="og:audio"\s+content="([^"]+)"', html)
        if match:
            return unescape(match.group(1)).replace("\\u002F", "/")
        match = re.search(r"\"audioPreview\"\s*:\s*\"([^\"]+)\"", html)
        if match:
            return unescape(match.group(1)).replace("\\u002F", "/")
        match = re.search(r"\"audioPreviewUrl\"\s*:\s*\"([^\"]+)\"", html)
        if match:
            return unescape(match.group(1)).replace("\\u002F", "/")
        match = re.search(r"\"preview_url\"\s*:\s*\"([^\"]+)\"", html)
        if match:
            return unescape(match.group(1)).replace("\\u002F", "/")
        return ""

    def _extract_meta_tags(self, html: str) -> dict:
        values = {}
        pattern = r'<meta\s+(?:property|name)="([^"]+)"\s+content="([^"]*)"'
        for match in re.finditer(pattern, html, re.IGNORECASE):
            key = (match.group(1) or "").strip()
            value = unescape((match.group(2) or "").strip())
            if not key or not value:
                continue
            if key not in values:
                values[key] = value
        return values

    def _artist_from_description(self, description: str) -> str:
        if not description:
            return None
        parts = [part.strip() for part in description.split("·") if part.strip()]
        if not parts:
            return None
        return parts[0]

    def _normalize_date(self, value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return None
        match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
        if match:
            return match.group(1)
        try:
            parsed = datetime.fromisoformat(raw)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            return None

    def _to_int(self, value):
        if value is None:
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _extract_rss_url(self, html: str) -> str:
        match = re.search(r"\"rssUrl\"\s*:\s*\"([^\"]+)\"", html)
        if match:
            return match.group(1).replace("\\u002F", "/")
        match = re.search(r"rel=\"alternate\"[^>]+type=\"application/rss\+xml\"[^>]+href=\"([^\"]+)\"", html)
        if match:
            return match.group(1)
        return ""

    def _parse_duration(self, value: str):
        if not value:
            return None
        match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
        if not match:
            return None
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds
