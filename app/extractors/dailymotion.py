import json
import logging
import re
from datetime import datetime, timezone

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class DailymotionExtractor(BaseExtractor):
    PLATFORM_ID = "dailymotion"
    REQUIRES_HEADLESS = False
    REQUIRES_PROXY = False
    TEST_URL = "https://www.dailymotion.com/video/x7tgad0"

    def extract(self, url: str) -> dict:
        video_id = self._extract_video_id(url)
        metadata = self._fetch_metadata(video_id)
        player_metadata = self._fetch_player_metadata(video_id)
        qualities = self._fetch_qualities(video_id, player_metadata=player_metadata)

        author = metadata.get("owner.screenname")
        if not author:
            author = ((player_metadata.get("owner") or {}).get("screenname"))

        manifest_url = self._first_manifest_url(qualities)
        is_hls = bool(manifest_url and not any(not item.get("is_hls") for item in qualities))

        return {
            "title": metadata.get("title") or "Dailymotion Video",
            "author": author,
            "channel_id": None,
            "thumbnail": metadata.get("thumbnail_url") or self._pick_thumbnail(player_metadata),
            "duration": metadata.get("duration") or player_metadata.get("duration"),
            "view_count": metadata.get("views_total") or player_metadata.get("views_total"),
            "description": metadata.get("description") or player_metadata.get("description"),
            "upload_date": self._format_upload_date(player_metadata.get("created_time")),
            "qualities": qualities,
            "subtitles": self._extract_subtitles(player_metadata),
            "chapters": [],
            "is_hls": is_hls,
            "manifest_url": manifest_url,
            "headers_required": self._build_request_headers(video_id),
        }

    def _extract_video_id(self, url: str) -> str:
        match = re.search(r"/video/([a-zA-Z0-9]+)", url)
        if match:
            return match.group(1)
        match = re.search(r"dai\.ly/([a-zA-Z0-9]+)", url)
        if match:
            return match.group(1)
        raise ExtractorError("Invalid Dailymotion URL", platform=self.PLATFORM_ID, url=url)

    def _fetch_metadata(self, video_id: str) -> dict:
        api_url = (
            "https://api.dailymotion.com/video/"
            f"{video_id}?fields=title,thumbnail_url,duration,views_total,description,owner.screenname"
        )
        try:
            response = self.http_get(api_url, headers=self._build_request_headers(video_id))
            return response.json()
        except Exception as exc:
            raise ExtractorError("Failed to fetch Dailymotion metadata", platform=self.PLATFORM_ID) from exc

    def _fetch_player_metadata(self, video_id: str) -> dict:
        metadata_url = f"https://www.dailymotion.com/player/metadata/video/{video_id}"
        try:
            response = self.http_get(metadata_url, headers=self._build_request_headers(video_id))
            payload = response.json()
            if isinstance(payload, dict):
                return payload
        except Exception as exc:
            logger.warning("Failed to fetch Dailymotion player metadata for %s: %s", video_id, exc)
        return {}

    def _fetch_qualities(self, video_id: str, player_metadata: dict = None) -> list:
        qualities = []
        seen_urls = set()

        metadata_payload = player_metadata or self._fetch_player_metadata(video_id)
        qualities_data = metadata_payload.get("qualities") or {}
        qualities.extend(self._qualities_from_map(qualities_data, seen_urls=seen_urls))

        if not qualities:
            html = self._fetch_embed_html(video_id)
            if html:
                qualities.extend(self._qualities_from_embed_config(html, seen_urls=seen_urls))
                if not qualities:
                    qualities.extend(self._qualities_from_embed_urls(html, seen_urls=seen_urls))

        if not qualities:
            raise ExtractorError("Dailymotion stream not found", platform=self.PLATFORM_ID)

        return self._sort_qualities(qualities)

    def _fetch_embed_html(self, video_id: str) -> str:
        embed_url = f"https://www.dailymotion.com/embed/video/{video_id}"
        try:
            response = self.http_get(embed_url, headers=self._build_request_headers(video_id))
            return response.text
        except Exception as exc:
            logger.warning("Failed to load Dailymotion embed page for %s: %s", video_id, exc)
            return ""

    def _qualities_from_embed_config(self, html: str, seen_urls: set) -> list:
        config_match = re.search(r"__PLAYER_CONFIG__\s*=\s*({.*?});", html, re.DOTALL)
        if not config_match:
            return []

        try:
            config = json.loads(config_match.group(1))
        except json.JSONDecodeError:
            return []

        return self._qualities_from_map(config.get("qualities") or {}, seen_urls=seen_urls)

    def _qualities_from_embed_urls(self, html: str, seen_urls: set) -> list:
        qualities = []
        raw_matches = []
        raw_matches.extend(re.findall(r"(https?://[^\s\"']+\.(?:mp4|m3u8)[^\s\"']*)", html, re.IGNORECASE))
        raw_matches.extend(re.findall(r"(https?:\\/\\/[^\s\"']+\.(?:mp4|m3u8)[^\s\"']*)", html, re.IGNORECASE))

        for index, raw_url in enumerate(raw_matches):
            stream_url = self._normalize_embedded_url(raw_url)
            if not stream_url or stream_url in seen_urls:
                continue
            is_hls = ".m3u8" in stream_url.lower()
            label = "auto" if is_hls else "original"
            selector = f"dm_embed_{label}_{index}"
            qualities.append(
                {
                    "label": label,
                    "display_label": label,
                    "selector": selector,
                    "format_id": selector,
                    "url": stream_url,
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": "m3u8" if is_hls else "mp4",
                    "is_hls": is_hls,
                }
            )
            seen_urls.add(stream_url)

        return qualities

    def _qualities_from_map(self, qualities_data: dict, seen_urls: set) -> list:
        qualities = []
        if not isinstance(qualities_data, dict):
            return qualities

        for raw_label, items in qualities_data.items():
            if not isinstance(items, list):
                continue
            normalized_label = self._normalize_quality_label(raw_label)
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue

                stream_url = self._normalize_embedded_url(item.get("url"))
                if not stream_url or stream_url in seen_urls:
                    continue

                stream_type = str(item.get("type") or "").strip().lower()
                is_hls = "mpegurl" in stream_type or ".m3u8" in stream_url.lower()
                format_value = "m3u8" if is_hls else self._guess_format(stream_url, stream_type)
                selector = f"dm_{str(raw_label).strip() or 'auto'}_{index}"

                qualities.append(
                    {
                        "label": normalized_label,
                        "display_label": normalized_label,
                        "selector": selector,
                        "format_id": selector,
                        "url": stream_url,
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": self._safe_int(item.get("bitrate")),
                        "hdr": False,
                        "format": format_value,
                        "is_hls": is_hls,
                    }
                )
                seen_urls.add(stream_url)

        return qualities

    def _extract_subtitles(self, player_metadata: dict) -> list:
        subtitles = []
        data = ((player_metadata or {}).get("subtitles") or {}).get("data") or {}
        if not isinstance(data, dict):
            return subtitles

        for lang, payload in data.items():
            if not isinstance(payload, dict):
                continue
            urls = payload.get("urls") or []
            if not isinstance(urls, list) or not urls:
                continue

            subtitle_url = self._normalize_embedded_url(urls[0])
            if not subtitle_url:
                continue

            normalized_lang = str(lang or "").strip().lower()
            subtitles.append(
                {
                    "lang": normalized_lang,
                    "label": str(payload.get("label") or normalized_lang or "subtitle"),
                    "format": self._guess_subtitle_format(subtitle_url),
                    "url": subtitle_url,
                }
            )

        return subtitles

    def _first_manifest_url(self, qualities: list):
        for item in qualities or []:
            stream_url = str(item.get("url") or "").strip()
            if not stream_url:
                continue
            if item.get("is_hls") or ".m3u8" in stream_url.lower():
                return stream_url
        return None

    def _pick_thumbnail(self, player_metadata: dict):
        thumbnails = (player_metadata or {}).get("thumbnails") or {}
        best_url = None
        best_rank = -1

        if isinstance(thumbnails, dict):
            for key, value in thumbnails.items():
                url_value = self._normalize_embedded_url(value)
                if not url_value:
                    continue
                rank = self._safe_int(key) or 0
                if rank >= best_rank:
                    best_rank = rank
                    best_url = url_value

        return best_url or self._normalize_embedded_url((player_metadata or {}).get("thumbnail_url"))

    def _build_request_headers(self, video_id: str) -> dict:
        headers = self.get_headers()
        headers.update(
            {
                "Accept": "application/json,text/plain,*/*",
                "Origin": "https://www.dailymotion.com",
                "Referer": f"https://www.dailymotion.com/video/{video_id}",
            }
        )
        return headers

    def _sort_qualities(self, qualities: list) -> list:
        return sorted(
            qualities,
            key=lambda item: (
                self._quality_rank(item.get("label")),
                item.get("bitrate") or 0,
            ),
            reverse=True,
        )

    def _quality_rank(self, label: str) -> int:
        normalized = str(label or "").strip().lower()
        if normalized in {"auto", "best", "source", "original"}:
            return 100000

        match = re.search(r"(\d+)", normalized)
        if not match:
            return 0

        value = int(match.group(1))
        if "k" in normalized and value < 100:
            value = value * 1000
        return value

    def _normalize_quality_label(self, label: str) -> str:
        raw_value = str(label or "").strip()
        normalized = raw_value.lower()
        if not normalized:
            return "auto"
        if normalized in {"auto", "best", "source", "original"}:
            return normalized
        if normalized in {"ld", "sd", "hq", "hd"}:
            return normalized.upper()
        if re.fullmatch(r"\d+", normalized):
            return f"{normalized}p"
        if re.fullmatch(r"\d+p", normalized):
            return normalized
        if re.fullmatch(r"\d+k", normalized):
            return f"{normalized[:-1]}K"
        return raw_value

    def _normalize_embedded_url(self, value) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = text.replace("\\u002F", "/").replace("\\u0026", "&")
        text = text.replace("\\/", "/")
        if text.startswith("http%3A") or text.startswith("https%3A"):
            try:
                from urllib.parse import unquote

                text = unquote(text)
            except Exception:
                return text
        return text

    def _guess_format(self, stream_url: str, stream_type: str = "") -> str:
        normalized_type = str(stream_type or "").lower()
        normalized_url = str(stream_url or "").lower()

        if "mpegurl" in normalized_type or ".m3u8" in normalized_url:
            return "m3u8"
        if "dash+xml" in normalized_type or ".mpd" in normalized_url:
            return "mpd"
        if "mp4" in normalized_type:
            return "mp4"

        match = re.search(r"\.([a-z0-9]{2,5})(?:\?|\Z)", normalized_url)
        if match:
            return match.group(1)
        return "mp4"

    def _guess_subtitle_format(self, subtitle_url: str) -> str:
        match = re.search(r"\.([a-z0-9]{2,5})(?:\?|\Z)", str(subtitle_url or "").lower())
        if match:
            return match.group(1)
        return "srt"

    def _safe_int(self, value):
        try:
            if value is None:
                return None
            return int(float(str(value)))
        except (TypeError, ValueError):
            return None

    def _format_upload_date(self, created_time) -> str:
        if created_time in {None, ""}:
            return None
        try:
            return datetime.fromtimestamp(int(created_time), tz=timezone.utc).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError, OverflowError):
            return None
