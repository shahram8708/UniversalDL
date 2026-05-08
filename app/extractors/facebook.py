import logging
import re
import time
from datetime import datetime

import yt_dlp

from playwright.sync_api import sync_playwright

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class FacebookExtractor(BaseExtractor):
    PLATFORM_ID = "facebook"
    REQUIRES_HEADLESS = True
    REQUIRES_PROXY = True
    TEST_URL = "https://www.facebook.com/watch/?v=123456789"

    IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".ico")
    AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac")
    VIDEO_EXTENSIONS = (".mp4", ".m4v", ".mov", ".webm", ".m3u8", ".mpd")

    HTML_VIDEO_KEYS = (
        ("playable_url_quality_hd", "1080p"),
        ("browser_native_hd_url", "1080p"),
        ("hd_src", "1080p"),
        ("playable_url", "720p"),
        ("browser_native_sd_url", "480p"),
        ("sd_src", "480p"),
    )

    HTML_AUDIO_KEYS = (
        ("dash_audio_url", "audio_only"),
        ("audio_url", "audio_only"),
    )

    def extract(self, url: str) -> dict:
        last_error = None

        for extractor in (self._extract_with_yt_dlp, self._extract_with_playwright):
            try:
                media_info = extractor(url)
                if not self._is_valid_media_info(media_info):
                    raise ExtractorError("Extractor returned invalid media metadata", platform=self.PLATFORM_ID, url=url)
                media_info.setdefault("platform", self.PLATFORM_ID)
                media_info.setdefault("content_type", "video")
                return media_info
            except Exception as exc:
                last_error = exc
                logger.warning("Facebook extraction attempt failed for %s: %s", url, exc)

        if isinstance(last_error, ExtractorError):
            raise last_error
        raise ExtractorError("Failed to extract Facebook video", platform=self.PLATFORM_ID, url=url) from last_error

    def _extract_with_yt_dlp(self, url: str) -> dict:
        options = {
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
            "skip_unavailable_fragments": True,
            "http_headers": {
                "User-Agent": self.USER_AGENT_POOL[0],
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.facebook.com/",
            },
        }

        proxy_url = self._resolve_proxy_url()
        if proxy_url:
            options["proxy"] = proxy_url

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise ExtractorError(str(exc), platform=self.PLATFORM_ID, url=url) from exc

        return self._build_from_yt_dlp_info(info, url)

    def _build_from_yt_dlp_info(self, info: dict, url: str) -> dict:
        if not isinstance(info, dict):
            raise ExtractorError("Facebook extractor returned empty metadata", platform=self.PLATFORM_ID, url=url)

        formats = info.get("formats") or []
        qualities = []
        seen = set()

        best_audio = None
        best_audio_bitrate = 0
        for fmt in formats:
            stream_url = self._clean_url(fmt.get("url"))
            if not stream_url:
                continue
            vcodec = str(fmt.get("vcodec") or "").lower()
            acodec = str(fmt.get("acodec") or "").lower()
            if vcodec != "none":
                continue
            if not acodec or acodec == "none":
                continue
            bitrate = fmt.get("abr") or fmt.get("tbr") or 0
            if bitrate > best_audio_bitrate:
                best_audio_bitrate = bitrate
                best_audio = fmt

        best_audio_url = self._clean_url((best_audio or {}).get("url")) if best_audio else None
        best_audio_size = None
        if best_audio:
            best_audio_size = best_audio.get("filesize") or best_audio.get("filesize_approx")

        for index, fmt in enumerate(formats):
            stream_url = self._clean_url(fmt.get("url"))
            if not stream_url or self._is_image_url(stream_url):
                continue

            vcodec = str(fmt.get("vcodec") or "").lower()
            acodec = str(fmt.get("acodec") or "").lower()
            has_video = bool(vcodec and vcodec != "none")
            has_audio = bool(acodec and acodec != "none")
            is_audio_only = not has_video and has_audio

            if not has_video and not is_audio_only:
                continue

            format_ext = str(fmt.get("ext") or "").strip().lower() or self._guess_format(stream_url)
            if not format_ext:
                format_ext = "mp4"

            label = "audio_only" if is_audio_only else self._label_for_height(fmt.get("height"))
            if not label and not is_audio_only:
                label = self._label_from_note(fmt.get("format_note") or fmt.get("resolution"))
            if not label:
                label = "original"

            format_id = str(fmt.get("format_id") or "").strip()
            selector = format_id or f"fb_{label}_{format_ext}_{index}"
            if selector in seen:
                continue
            seen.add(selector)

            bitrate = fmt.get("tbr") or fmt.get("abr")
            bitrate_bps = None
            if bitrate:
                try:
                    bitrate_bps = int(float(bitrate) * 1000)
                except (TypeError, ValueError):
                    bitrate_bps = None

            fps = fmt.get("fps")
            fps_value = None
            if fps:
                try:
                    fps_value = int(round(float(fps)))
                except (TypeError, ValueError):
                    fps_value = None

            size_bytes = fmt.get("filesize") or fmt.get("filesize_approx")
            if has_video and not has_audio and size_bytes and best_audio_size:
                size_bytes = size_bytes + best_audio_size

            hdr_note = str(fmt.get("dynamic_range") or fmt.get("format_note") or "").lower()
            is_hls = format_ext == "m3u8" or ".m3u8" in stream_url.lower()

            quality = {
                "label": label,
                "display_label": "",
                "selector": selector,
                "format_id": format_id or None,
                "url": stream_url,
                "size_bytes": size_bytes,
                "codec": acodec if is_audio_only else (vcodec if has_video else acodec),
                "bitrate": bitrate_bps,
                "hdr": "hdr" in hdr_note,
                "fps": fps_value,
                "format": "m3u8" if is_hls else format_ext,
                "has_audio": has_audio,
                "is_hls": is_hls,
            }

            if has_video and not has_audio and best_audio_url:
                quality["audio_url"] = best_audio_url

            quality["display_label"] = self._build_display_label(quality, is_audio_only)
            qualities.append(quality)

        if not qualities:
            fallback_url = self._clean_url(info.get("url"))
            if fallback_url and not self._is_image_url(fallback_url):
                fallback_is_hls = ".m3u8" in fallback_url.lower()
                qualities.append(
                    {
                        "label": "original",
                        "display_label": "Original",
                        "selector": "fb_original",
                        "format_id": None,
                        "url": fallback_url,
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "fps": None,
                        "format": "m3u8" if fallback_is_hls else self._guess_format(fallback_url),
                        "has_audio": True,
                        "is_hls": fallback_is_hls,
                    }
                )

        if not qualities:
            raise ExtractorError("Facebook video stream not found", platform=self.PLATFORM_ID, url=url)

        qualities = self._sort_qualities(qualities)

        upload_date = None
        raw_upload_date = str(info.get("upload_date") or "").strip()
        if raw_upload_date:
            try:
                upload_date = datetime.strptime(raw_upload_date, "%Y%m%d").strftime("%Y-%m-%d")
            except ValueError:
                upload_date = None

        manifest_url = None
        for quality in qualities:
            quality_url = str(quality.get("url") or "")
            if quality.get("is_hls") or ".m3u8" in quality_url.lower():
                manifest_url = quality_url
                break

        headers_required = info.get("http_headers") or {}
        if not headers_required:
            headers_required = {
                "User-Agent": self.USER_AGENT_POOL[0],
                "Referer": "https://www.facebook.com/",
            }

        return {
            "title": info.get("title") or "Facebook Video",
            "author": info.get("uploader"),
            "channel_id": info.get("channel_id") or info.get("uploader_id"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "description": (info.get("description") or "")[:500] if info.get("description") else None,
            "upload_date": upload_date,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": bool(manifest_url),
            "manifest_url": manifest_url,
            "headers_required": headers_required,
            "content_type": "video",
        }

    def _extract_with_playwright(self, url: str) -> dict:
        html_content = ""
        title = None
        thumbnail = None
        video_candidates = []
        audio_candidates = []
        seen_urls = set()

        def add_candidate(collection: list, value: str, label: str, is_audio: bool = False):
            clean_value = self._clean_url(value)
            if not clean_value or self._is_image_url(clean_value):
                return
            key = clean_value.lower()
            if key in seen_urls:
                return
            seen_urls.add(key)
            is_hls = ".m3u8" in clean_value.lower()
            collection.append(
                {
                    "url": clean_value,
                    "label": label,
                    "is_audio": is_audio,
                    "is_hls": is_hls,
                }
            )

        def handle_response(response):
            response_url = self._clean_url(response.url)
            if not response_url:
                return

            content_type = ""
            try:
                content_type = str(response.headers.get("content-type") or "").lower()
            except Exception:
                content_type = ""

            if self._is_image_content_type(content_type):
                return

            looks_like_video = self._looks_like_video_url(response_url) or "video" in content_type or "mpegurl" in content_type
            looks_like_audio = self._looks_like_audio_url(response_url) or "audio" in content_type

            if looks_like_audio:
                add_candidate(audio_candidates, response_url, "audio_only", is_audio=True)
            elif looks_like_video:
                add_candidate(video_candidates, response_url, "original")

        browser = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=self.USER_AGENT_POOL[0])
                page = context.new_page()
                page.on("response", handle_response)
                page.goto(url, wait_until="networkidle", timeout=45000)

                if "login" in page.url.lower():
                    raise ExtractorError(
                        "This Facebook content requires login. Only public videos are supported.",
                        platform=self.PLATFORM_ID,
                        url=url,
                    )

                time.sleep(2)
                html_content = page.content()

            for key, label in self.HTML_VIDEO_KEYS:
                value = self._extract_json_value(html_content, key)
                if value:
                    add_candidate(video_candidates, value, label)

            for key, label in self.HTML_AUDIO_KEYS:
                value = self._extract_json_value(html_content, key)
                if value:
                    add_candidate(audio_candidates, value, label, is_audio=True)

            og_video = self._extract_meta(html_content, "og:video")
            og_video_secure = self._extract_meta(html_content, "og:video:secure_url")
            add_candidate(video_candidates, og_video, "original")
            add_candidate(video_candidates, og_video_secure, "original")

            manifest_url = self._extract_manifest_url(html_content)
            add_candidate(video_candidates, manifest_url, "adaptive_hls")

            title = self._extract_meta(html_content, "og:title")
            thumbnail = self._extract_meta(html_content, "og:image")

            qualities = []
            best_audio = audio_candidates[0]["url"] if audio_candidates else None
            for index, candidate in enumerate(video_candidates):
                quality = {
                    "label": candidate["label"] or "original",
                    "display_label": candidate["label"] or "original",
                    "selector": f"fb_playwright_video_{index}",
                    "format_id": None,
                    "url": candidate["url"],
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "fps": None,
                    "format": "m3u8" if candidate["is_hls"] else self._guess_format(candidate["url"]),
                    "has_audio": True,
                    "is_hls": candidate["is_hls"],
                }
                if best_audio and not candidate["is_hls"]:
                    quality["audio_url"] = best_audio
                qualities.append(quality)

            for index, candidate in enumerate(audio_candidates):
                qualities.append(
                    {
                        "label": "audio_only",
                        "display_label": "Audio",
                        "selector": f"fb_playwright_audio_{index}",
                        "format_id": None,
                        "url": candidate["url"],
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "fps": None,
                        "format": self._guess_format(candidate["url"]),
                        "has_audio": True,
                        "is_hls": candidate["is_hls"],
                    }
                )

            qualities = self._sort_qualities(qualities)
            if not qualities:
                raise ExtractorError("Facebook video stream not found", platform=self.PLATFORM_ID, url=url)

            resolved_manifest = None
            for quality in qualities:
                quality_url = str(quality.get("url") or "")
                if quality.get("is_hls") or ".m3u8" in quality_url.lower():
                    resolved_manifest = quality_url
                    break

            return {
                "title": title or "Facebook Video",
                "author": None,
                "channel_id": None,
                "thumbnail": thumbnail,
                "duration": None,
                "view_count": None,
                "description": None,
                "upload_date": None,
                "qualities": qualities,
                "subtitles": [],
                "chapters": [],
                "is_hls": bool(resolved_manifest),
                "manifest_url": resolved_manifest,
                "headers_required": {
                    "User-Agent": self.USER_AGENT_POOL[0],
                    "Referer": "https://www.facebook.com/",
                },
                "content_type": "video",
            }
        except ExtractorError:
            raise
        except Exception as exc:
            raise ExtractorError("Failed to extract Facebook video", platform=self.PLATFORM_ID, url=url) from exc
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    logger.debug("Ignoring browser close error in facebook extractor", exc_info=True)

    def _extract_json_value(self, html: str, key: str) -> str:
        patterns = [
            rf'"{re.escape(key)}":"([^\"]+)"',
            rf'"{re.escape(key)}":\s*"([^\"]+)"',
            rf"'{re.escape(key)}':'([^']+)'",
            rf'"{re.escape(key)}":\s*"(https?://[^\"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return self._clean_url(match.group(1))
        return None

    def _extract_manifest_url(self, html: str) -> str:
        match = re.search(r'https?:\\/\\/[^"\']+\.m3u8[^"\']*', html)
        if match:
            return self._clean_url(match.group(0))
        match = re.search(r'https?://[^"\']+\.m3u8[^"\']*', html)
        if match:
            return self._clean_url(match.group(0))
        return None

    def _extract_meta(self, html: str, property_name: str) -> str:
        pattern = rf'<meta[^>]+property="{re.escape(property_name)}"[^>]+content="([^\"]+)"'
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return self._clean_url(match.group(1)) if "video" in property_name else match.group(1)
        return None

    def _clean_url(self, value) -> str:
        text = str(value or "").strip().strip("\"'")
        if not text:
            return None

        text = text.replace("\\/", "/").replace("\\u0025", "%").replace("&amp;", "&")
        text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda item: chr(int(item.group(1), 16)), text)

        if text.startswith("//"):
            text = f"https:{text}"
        if text.startswith("http://"):
            text = f"https://{text[7:]}"
        if not (text.startswith("https://") or text.startswith("http://")):
            return None
        return text

    def _is_image_content_type(self, content_type: str) -> bool:
        text = str(content_type or "").strip().lower()
        return text.startswith("image/")

    def _looks_like_video_url(self, url: str) -> bool:
        value = str(url or "").strip().lower()
        if not value:
            return False
        if any(ext in value for ext in (".m3u8", ".mpd", ".mp4", ".mov", ".m4v", ".webm")):
            return True
        if "video" in value or "/v/" in value:
            return True
        return False

    def _looks_like_audio_url(self, url: str) -> bool:
        value = str(url or "").strip().lower()
        if not value:
            return False
        if any(ext in value for ext in (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac")):
            return True
        if "audio" in value and "video" not in value:
            return True
        return False

    def _is_image_url(self, url: str) -> bool:
        value = str(url or "").strip().lower().split("?", 1)[0]
        if not value:
            return True
        return any(value.endswith(ext) for ext in self.IMAGE_EXTENSIONS)

    def _guess_format(self, url: str) -> str:
        value = str(url or "").strip().lower().split("?", 1)[0]
        for ext in self.VIDEO_EXTENSIONS + self.AUDIO_EXTENSIONS + self.IMAGE_EXTENSIONS:
            if value.endswith(ext):
                return ext.lstrip(".")
        if ".m3u8" in value:
            return "m3u8"
        return "mp4"

    def _label_for_height(self, height) -> str:
        if not height:
            return None
        try:
            parsed = int(height)
        except (TypeError, ValueError):
            return None

        if parsed >= 2160:
            return "4K"
        if parsed >= 1440:
            return "2K"
        if parsed >= 1080:
            return "1080p"
        if parsed >= 720:
            return "720p"
        if parsed >= 480:
            return "480p"
        if parsed >= 360:
            return "360p"
        return f"{parsed}p"

    def _label_from_note(self, note) -> str:
        text = str(note or "").strip().lower()
        if not text:
            return None
        if "4k" in text:
            return "4K"
        if "2k" in text:
            return "2K"

        match = re.search(r"(\d{3,4})p", text)
        if match:
            return f"{match.group(1)}p"
        if "hd" in text:
            return "1080p"
        if "sd" in text:
            return "480p"
        return None

    def _build_display_label(self, quality: dict, is_audio_only: bool) -> str:
        parts = []
        label = quality.get("label")
        fps = quality.get("fps")
        bitrate = quality.get("bitrate")

        if is_audio_only:
            parts.append("Audio")
        else:
            parts.append(label if label and label != "original" else "Video")
            if fps and fps >= 50:
                parts.append(f"{fps}fps")
            if quality.get("hdr"):
                parts.append("HDR")
            if not quality.get("has_audio"):
                parts.append("Video Only")

        if bitrate:
            parts.append(f"{int(bitrate / 1000)} kbps")

        fmt = str(quality.get("format") or "").strip().upper()
        if fmt and fmt != "M3U8":
            parts.append(fmt)

        return " ".join(parts) if parts else ("Audio" if is_audio_only else "Video")

    def _sort_qualities(self, qualities: list) -> list:
        return sorted(qualities, key=self._quality_sort_key)

    def _quality_sort_key(self, item: dict):
        label = str(item.get("label") or "").strip().lower()
        bitrate = item.get("bitrate") or 0
        fps = item.get("fps") or 0

        if label in {"audio", "audio_only", "audio only"}:
            return (2, 0, 0, -bitrate)

        height = self._height_from_label(label)
        if label == "original" and height == 0:
            height = 9999
        return (0, -height, -fps, -bitrate)

    def _height_from_label(self, label: str) -> int:
        value = str(label or "").strip().lower()
        if value == "4k":
            return 2160
        if value == "2k":
            return 1440

        match = re.search(r"(\d{3,4})p", value)
        if not match:
            return 0
        try:
            return int(match.group(1))
        except ValueError:
            return 0

    def _is_valid_media_info(self, media_info: dict) -> bool:
        if not isinstance(media_info, dict):
            return False
        qualities = media_info.get("qualities")
        if not isinstance(qualities, list) or not qualities:
            return False

        valid_urls = []
        for quality in qualities:
            if not isinstance(quality, dict):
                continue
            clean = self._clean_url(quality.get("url"))
            if not clean:
                continue
            if self._is_image_url(clean):
                continue
            valid_urls.append(clean)

        return bool(valid_urls)
