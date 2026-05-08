import logging
import re
from datetime import datetime

import yt_dlp

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class VimeoExtractor(BaseExtractor):
    PLATFORM_ID = "vimeo"
    REQUIRES_HEADLESS = False
    REQUIRES_PROXY = False
    TEST_URL = "https://vimeo.com/148751763"

    _TERMINAL_ERROR_MARKERS = (
        "private",
        "forbidden",
        "password",
        "drm",
        "not available",
        "access denied",
        "purchase",
        "rent",
    )

    def extract(self, url: str) -> dict:
        yt_dlp_error = None

        video_id = self._extract_video_id(url)

        if self._should_prefer_yt_dlp(url):
            try:
                return self._extract_with_yt_dlp(url)
            except Exception as exc:
                yt_dlp_error = exc
                logger.warning("Vimeo yt-dlp extraction failed for %s: %s", url, exc)

        config_error = None

        try:
            return self._extract_from_config(url, video_id)
        except Exception as exc:
            config_error = exc
            logger.warning("Vimeo config extraction failed for %s: %s", url, exc)

        if yt_dlp_error is not None:
            if isinstance(yt_dlp_error, ExtractorError):
                raise yt_dlp_error
            raise ExtractorError("Failed to extract Vimeo media", platform=self.PLATFORM_ID, url=url) from yt_dlp_error

        try:
            return self._extract_with_yt_dlp(url)
        except Exception as exc:
            if isinstance(exc, ExtractorError):
                raise
            if isinstance(config_error, ExtractorError):
                raise config_error
            raise ExtractorError("Failed to extract Vimeo media", platform=self.PLATFORM_ID, url=url) from exc

    def _extract_from_config(self, url: str, video_id: str) -> dict:
        config_url = f"https://player.vimeo.com/video/{video_id}/config"
        headers = {
            "Referer": url,
            "Origin": "https://vimeo.com",
            "Accept": "application/json, text/plain, */*",
        }
        try:
            response = self.http_get(config_url, headers=headers)
            config = response.json()
        except Exception as exc:
            raise ExtractorError("Failed to fetch Vimeo config", platform=self.PLATFORM_ID, url=url) from exc

        if config.get("message"):
            raise ExtractorError("This Vimeo video is private or restricted", platform=self.PLATFORM_ID, url=url)

        video = config.get("video") or {}
        owner = video.get("owner") or {}
        thumbs = video.get("thumbs") or {}
        thumbnail = thumbs.get("1280") or next(iter(thumbs.values()), None)

        request = config.get("request") or {}
        files = request.get("files") or {}
        progressive = files.get("progressive") or []
        qualities = []
        for index, item in enumerate(progressive):
            quality_label = item.get("quality") or self._label_for_height(item.get("height")) or "original"
            bitrate = item.get("bitrate") or item.get("avg_bitrate")
            bitrate_bps = self._to_bps(bitrate)
            selector = str(item.get("id") or item.get("quality") or f"vimeo_{index}")
            format_value = (item.get("mime") or "video/mp4").split("/")[-1]

            qualities.append(
                {
                    "label": quality_label,
                    "display_label": quality_label,
                    "selector": selector,
                    "format_id": str(item.get("id")) if item.get("id") is not None else None,
                    "url": item.get("url"),
                    "size_bytes": item.get("size") or item.get("filesize"),
                    "codec": None,
                    "bitrate": bitrate_bps,
                    "hdr": False,
                    "format": format_value,
                    "has_audio": True,
                }
            )

        manifest_url = None
        hls = files.get("hls") or {}
        default_cdn = hls.get("default_cdn")
        cdns = hls.get("cdns") or {}
        if cdns:
            selected_cdn = cdns.get(default_cdn) if default_cdn else None
            if not selected_cdn:
                selected_cdn = next(iter(cdns.values()))
            manifest_url = selected_cdn.get("url")

        if manifest_url and not qualities:
            qualities.append(
                {
                    "label": "best",
                    "display_label": "best",
                    "selector": "best",
                    "format_id": None,
                    "url": manifest_url,
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": "mp4",
                    "is_hls": True,
                    "has_audio": True,
                }
            )

        if not qualities and not manifest_url:
            raise ExtractorError("No downloadable Vimeo formats available", platform=self.PLATFORM_ID, url=url)

        qualities = self._sort_qualities(qualities)

        subtitles = []
        for track in (request.get("text_tracks") or []):
            subtitle_url = track.get("url")
            if subtitle_url and subtitle_url.startswith("/"):
                subtitle_url = "https://player.vimeo.com" + subtitle_url
            subtitles.append(
                {
                    "lang": track.get("lang") or "en",
                    "label": track.get("label") or track.get("lang") or "en",
                    "url": subtitle_url,
                    "format": track.get("type") or "vtt",
                }
            )

        upload_date = self._normalize_upload_date(video.get("release_time") or video.get("created_time"))

        return {
            "title": video.get("title"),
            "author": owner.get("name"),
            "channel_id": None,
            "thumbnail": thumbnail,
            "duration": video.get("duration"),
            "view_count": video.get("stats_number_of_plays"),
            "description": video.get("description"),
            "upload_date": upload_date,
            "qualities": qualities,
            "subtitles": subtitles,
            "chapters": [],
            "is_hls": bool(manifest_url and not progressive),
            "manifest_url": manifest_url,
            "headers_required": {"Referer": url, "Origin": "https://vimeo.com"},
        }

    def _extract_with_yt_dlp(self, url: str) -> dict:
        headers = self.get_headers()
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "noplaylist": True,
            "format": "bestvideo+bestaudio/best",
            "socket_timeout": 30,
            "retries": 2,
            "fragment_retries": 2,
            "extractor_retries": 2,
            "http_headers": {
                "User-Agent": headers.get("User-Agent"),
                "Accept-Language": headers.get("Accept-Language", "en-US,en;q=0.9"),
                "Referer": url,
            },
        }

        proxy_url = self._get_proxy_url()
        if proxy_url:
            ydl_opts["proxy"] = proxy_url

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise ExtractorError(self._friendly_download_error(str(exc)), platform=self.PLATFORM_ID, url=url) from exc

        return self._build_from_yt_dlp_info(info, url)

    def _build_from_yt_dlp_info(self, info: dict, url: str) -> dict:
        formats = info.get("formats") or []

        best_audio_format = None
        best_audio_bitrate = 0
        for fmt in formats:
            if not fmt.get("url"):
                continue
            if not self._is_audio_only_format(fmt):
                continue
            bitrate = fmt.get("abr") or fmt.get("tbr") or 0
            if best_audio_format is None or bitrate > best_audio_bitrate:
                best_audio_bitrate = bitrate
                best_audio_format = fmt

        best_audio_url = best_audio_format.get("url") if best_audio_format else None
        best_audio_size = None
        if best_audio_format:
            best_audio_size = best_audio_format.get("filesize") or best_audio_format.get("filesize_approx")

        qualities = []
        seen_selectors = set()
        for index, fmt in enumerate(formats):
            media_url = fmt.get("url")
            if not media_url:
                continue

            vcodec = fmt.get("vcodec")
            acodec = fmt.get("acodec")
            height = fmt.get("height")
            is_audio_only = self._is_audio_only_format(fmt)
            label = "audio_only" if is_audio_only else self._label_for_height(height)
            if not label and not is_audio_only:
                continue

            format_id = str(fmt.get("format_id") or "").strip()
            selector = format_id or f"vimeo_{index}_{fmt.get('ext') or 'bin'}"
            if selector in seen_selectors:
                continue
            seen_selectors.add(selector)

            bitrate = fmt.get("tbr") or fmt.get("abr")
            bitrate_bps = self._to_bps(bitrate)
            size_bytes = fmt.get("filesize") or fmt.get("filesize_approx")
            has_audio = True if is_audio_only else bool(str(acodec or "").strip().lower() not in {"", "none"})
            if not has_audio and best_audio_size and size_bytes:
                size_bytes = size_bytes + best_audio_size

            entry = {
                "label": label,
                "display_label": label,
                "selector": selector,
                "format_id": format_id or None,
                "url": media_url,
                "size_bytes": size_bytes,
                "codec": acodec if is_audio_only else (vcodec if vcodec != "none" else acodec),
                "bitrate": bitrate_bps,
                "hdr": "hdr" in str(fmt.get("dynamic_range") or "").lower(),
                "format": fmt.get("ext") or "mp4",
                "has_audio": has_audio,
            }
            protocol = str(fmt.get("protocol") or "").lower()
            if protocol in {"m3u8", "m3u8_native"} or ".m3u8" in str(media_url):
                entry["is_hls"] = True

            if not is_audio_only and not has_audio and best_audio_url:
                entry["audio_url"] = best_audio_url

            qualities.append(entry)

        if not qualities and info.get("url"):
            single_url = info.get("url")
            single_format = info.get("ext") or "mp4"
            qualities.append(
                {
                    "label": "best",
                    "display_label": "best",
                    "selector": str(info.get("format_id") or "best"),
                    "format_id": str(info.get("format_id")) if info.get("format_id") else None,
                    "url": single_url,
                    "size_bytes": info.get("filesize") or info.get("filesize_approx"),
                    "codec": None,
                    "bitrate": self._to_bps(info.get("tbr") or info.get("abr")),
                    "hdr": False,
                    "format": single_format,
                    "is_hls": ".m3u8" in str(single_url),
                    "has_audio": True,
                }
            )

        if not qualities:
            raise ExtractorError("No downloadable Vimeo formats available", platform=self.PLATFORM_ID, url=url)

        qualities = self._sort_qualities(qualities)

        subtitles = []
        subtitles.extend(self._parse_subtitles(info.get("subtitles") or {}))
        subtitles.extend(self._parse_subtitles(info.get("automatic_captions") or {}))

        chapters = []
        for chapter in info.get("chapters") or []:
            start = chapter.get("start_time") or 0
            chapters.append({"title": chapter.get("title") or "Chapter", "start_ms": int(start * 1000)})

        manifest_url = None
        for fmt in formats:
            url_value = fmt.get("url") or ""
            if ".m3u8" in url_value:
                manifest_url = url_value
                break

        headers_required = info.get("http_headers") or {}
        headers_required.setdefault("Referer", url)

        return {
            "title": info.get("title"),
            "author": info.get("uploader") or info.get("channel"),
            "channel_id": info.get("channel_id"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "description": (info.get("description") or "")[:500] if info.get("description") else None,
            "upload_date": self._normalize_upload_date(info.get("upload_date")),
            "qualities": qualities,
            "subtitles": subtitles,
            "chapters": chapters,
            "is_hls": bool(manifest_url and not any(not q.get("is_hls") for q in qualities)),
            "manifest_url": manifest_url,
            "headers_required": headers_required,
        }

    def _extract_video_id(self, url: str) -> str:
        patterns = [
            r"player\.vimeo\.com/video/(\d+)",
            r"vimeo\.com/ondemand/[^/?#]+/(\d+)",
            r"vimeo\.com/channels/[^/?#]+/(\d+)",
            r"vimeo\.com/groups/[^/?#]+/videos/(\d+)",
            r"vimeo\.com/(?:album|showcase)/\d+/video/(\d+)",
            r"vimeo\.com/(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        fallback = re.search(r"/(\d+)(?:$|[/?#])", url)
        if fallback and "vimeo.com" in url:
            return fallback.group(1)
        raise ExtractorError("Invalid Vimeo URL", platform=self.PLATFORM_ID, url=url)

    def _parse_subtitles(self, subtitles_data: dict) -> list:
        items = []
        for lang, entries in subtitles_data.items():
            for item in entries or []:
                subtitle_url = item.get("url")
                if not subtitle_url:
                    continue
                ext = item.get("ext") or "vtt"
                items.append(
                    {
                        "lang": lang,
                        "label": lang,
                        "url": subtitle_url,
                        "format": ext,
                    }
                )
        return items

    def _label_for_height(self, height):
        try:
            value = int(height)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return f"{value}p"

    def _to_bps(self, value):
        if value is None:
            return None
        try:
            return int(float(value) * 1000)
        except (TypeError, ValueError):
            return None

    def _quality_sort_key(self, quality: dict):
        label = str(quality.get("label") or "").lower()
        is_audio = label in {"audio", "audio_only", "audio only"}
        height_match = re.search(r"(\d{3,4})p", label)
        height = int(height_match.group(1)) if height_match else 0
        bitrate = quality.get("bitrate") or 0
        return (1 if is_audio else 0, -height, -int(bitrate))

    def _sort_qualities(self, qualities: list) -> list:
        return sorted(qualities, key=self._quality_sort_key)

    def _normalize_upload_date(self, value):
        raw = str(value or "").strip()
        if not raw:
            return None
        if re.fullmatch(r"\d{8}", raw):
            try:
                return datetime.strptime(raw, "%Y%m%d").strftime("%Y-%m-%d")
            except ValueError:
                return None
        try:
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            parsed = datetime.fromisoformat(raw)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            return None

    def _get_proxy_url(self):
        if isinstance(self.proxy, dict):
            return self.proxy.get("https://") or self.proxy.get("http://")
        if isinstance(self.proxy, str):
            return self.proxy
        return None

    def _friendly_download_error(self, message: str) -> str:
        lowered = (message or "").lower()
        if any(marker in lowered for marker in self._TERMINAL_ERROR_MARKERS):
            return "This Vimeo video is private, purchase protected, or unavailable."
        return message or "Failed to extract Vimeo media"

    def _should_prefer_yt_dlp(self, url: str) -> bool:
        value = str(url or "").lower()
        return "/ondemand/" in value

    def _is_audio_only_format(self, fmt: dict) -> bool:
        vcodec = str(fmt.get("vcodec") or "").strip().lower()
        acodec = str(fmt.get("acodec") or "").strip().lower()
        height = fmt.get("height")
        format_id = str(fmt.get("format_id") or "").strip().lower()
        format_note = str(fmt.get("format_note") or "").strip().lower()

        if vcodec in {"none", "audio only"}:
            if height is None:
                return True
            return acodec not in {"", "none"}

        if height is None and acodec not in {"", "none"} and "audio" in vcodec:
            return True

        if height is None and ("audio" in format_id or "audio" in format_note):
            return True

        return False
