import json
import logging
import re
import time
from datetime import datetime
from html import unescape
from urllib.parse import unquote

import yt_dlp
from playwright.sync_api import sync_playwright

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class LinkedInExtractor(BaseExtractor):
    PLATFORM_ID = "linkedin"
    REQUIRES_HEADLESS = True
    REQUIRES_PROXY = False
    TEST_URL = "https://www.linkedin.com/posts/microsoft_activity-1234567890/"

    def extract(self, url: str) -> dict:
        last_error = None

        extractors = [self._extract_with_yt_dlp, self._extract_with_embed_endpoint, self._extract_with_playwright]
        if self._is_profile_or_company_url(url):
            extractors.insert(0, self._extract_profile_image_with_playwright)

        for extractor in extractors:
            try:
                media_info = extractor(url)
                if not self._is_valid_media_info(media_info):
                    raise ExtractorError("Extractor returned invalid media metadata", platform=self.PLATFORM_ID, url=url)
                media_info.setdefault("platform", self.PLATFORM_ID)
                media_info.setdefault("content_type", self._infer_content_type(media_info.get("qualities") or []))
                return media_info
            except Exception as exc:
                last_error = exc
                logger.warning("LinkedIn extraction attempt failed for %s: %s", url, exc)

        if isinstance(last_error, ExtractorError):
            raise last_error
        raise ExtractorError("Failed to extract LinkedIn media", platform=self.PLATFORM_ID, url=url) from last_error

    def _extract_profile_image_with_playwright(self, url: str) -> dict:
        browser = None
        html_content = ""
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=self.USER_AGENT_POOL[0])
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                html_content = page.content()

                image_candidates = []
                image_candidates.append(self._extract_meta(html_content, "og:image"))
                image_candidates.append(self._extract_meta(html_content, "twitter:image"))

                for match in re.finditer(
                    r"<img[^>]+(?:src|data-delayed-url)=['\"]([^'\"]+)['\"]",
                    html_content,
                    re.IGNORECASE,
                ):
                    image_candidates.append(self._clean_url(match.group(1)))

                profile_image_url = self._pick_profile_image_url(image_candidates)
                if not profile_image_url:
                    raise ExtractorError(
                        "LinkedIn profile media not found. Use a public post URL for video or post media downloads.",
                        platform=self.PLATFORM_ID,
                        url=url,
                    )

                title = self._extract_meta(html_content, "og:title") or self._extract_title(html_content) or "LinkedIn Profile"
                description = self._extract_meta(html_content, "og:description")

                return {
                    "title": title,
                    "author": None,
                    "channel_id": None,
                    "thumbnail": profile_image_url,
                    "duration": None,
                    "view_count": None,
                    "description": description,
                    "upload_date": None,
                    "qualities": [
                        {
                            "label": "image",
                            "display_label": "image",
                            "selector": "linkedin_profile_image",
                            "format_id": None,
                            "url": profile_image_url,
                            "size_bytes": None,
                            "codec": None,
                            "bitrate": None,
                            "hdr": False,
                            "format": "jpg",
                            "has_audio": False,
                            "is_hls": False,
                        }
                    ],
                    "subtitles": [],
                    "chapters": [],
                    "is_hls": False,
                    "manifest_url": None,
                    "headers_required": {
                        "Referer": "https://www.linkedin.com/",
                        "Origin": "https://www.linkedin.com",
                        "User-Agent": self.USER_AGENT_POOL[0],
                    },
                    "content_type": "image",
                }
        except ExtractorError:
            raise
        except Exception as exc:
            raise ExtractorError("Failed to extract LinkedIn profile image", platform=self.PLATFORM_ID, url=url) from exc
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    logger.debug("Ignoring browser close error in linkedin profile extractor", exc_info=True)

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
                "Referer": "https://www.linkedin.com/",
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

    def _extract_with_embed_endpoint(self, url: str) -> dict:
        urn_ids = self._extract_urn_ids(url)
        if not urn_ids:
            raise ExtractorError("LinkedIn embed id not found", platform=self.PLATFORM_ID, url=url)

        last_error = None
        for urn_type, urn_id in urn_ids:
            endpoint = f"https://www.linkedin.com/embed/feed/update/urn:li:{urn_type}:{urn_id}"
            try:
                response = self.http_get(endpoint, headers=self.get_headers())
                html_content = response.text or ""
                candidates = self._extract_media_candidates_from_html(html_content)
                if not candidates:
                    continue
                return self._build_media_info_from_candidates(url, html_content, candidates)
            except Exception as exc:
                last_error = exc
                continue

        if last_error is not None:
            raise ExtractorError("LinkedIn embed media not found", platform=self.PLATFORM_ID, url=url) from last_error
        raise ExtractorError("LinkedIn embed media not found", platform=self.PLATFORM_ID, url=url)

    def _extract_with_playwright(self, url: str) -> dict:
        html_content = ""
        response_candidates = []
        seen_urls = set()

        def add_candidate(stream_url: str, content_type: str = ""):
            cleaned = self._clean_url(stream_url)
            if not cleaned:
                return
            if cleaned in seen_urls:
                return
            if not self._looks_like_media_candidate(cleaned, content_type):
                return
            seen_urls.add(cleaned)
            response_candidates.append(
                {
                    "url": cleaned,
                    "content_type": str(content_type or "").lower(),
                }
            )

        def handle_response(response):
            try:
                add_candidate(response.url, (response.headers or {}).get("content-type", ""))
            except Exception:
                return

        browser = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=self.USER_AGENT_POOL[0])
                page = context.new_page()
                page.on("response", handle_response)

                page.goto(url, wait_until="domcontentloaded", timeout=45000)

                current_url = str(page.url or "").lower()
                if any(token in current_url for token in ("/login", "authwall", "checkpoint")):
                    raise ExtractorError(
                        "This LinkedIn content requires login. Only publicly accessible posts can be downloaded.",
                        platform=self.PLATFORM_ID,
                        url=url,
                    )

                deadline = time.time() + 8
                while time.time() < deadline:
                    time.sleep(0.2)

                html_content = page.content()
                for candidate in self._extract_media_candidates_from_html(html_content):
                    add_candidate(candidate.get("url"), candidate.get("content_type"))

                if not response_candidates:
                    raise ExtractorError("LinkedIn media not found", platform=self.PLATFORM_ID, url=url)

                return self._build_media_info_from_candidates(url, html_content, response_candidates)
        except ExtractorError:
            raise
        except Exception as exc:
            raise ExtractorError("Failed to extract LinkedIn media", platform=self.PLATFORM_ID, url=url) from exc
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    logger.debug("Ignoring browser close error in linkedin extractor", exc_info=True)

    def _build_from_yt_dlp_info(self, info: dict, url: str) -> dict:
        if not isinstance(info, dict):
            raise ExtractorError("LinkedIn extractor returned empty metadata", platform=self.PLATFORM_ID, url=url)

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
            media_url = self._clean_url(fmt.get("url"))
            if not media_url:
                continue

            is_audio_only = self._is_audio_only_format(fmt)
            label = "audio_only" if is_audio_only else self._label_for_height(fmt.get("height"))
            if not label and not is_audio_only:
                label = self._label_from_note(fmt.get("format_note") or fmt.get("resolution"))
            if not label:
                label = "original"

            format_id = str(fmt.get("format_id") or "").strip()
            selector = format_id or f"linkedin_{index}_{fmt.get('ext') or 'bin'}"
            if selector in seen_selectors:
                continue
            seen_selectors.add(selector)

            bitrate = fmt.get("tbr") or fmt.get("abr")
            bitrate_bps = self._to_bps(bitrate)
            size_bytes = fmt.get("filesize") or fmt.get("filesize_approx")

            acodec = str(fmt.get("acodec") or "").strip().lower()
            has_audio = True if is_audio_only else acodec not in {"", "none"}
            if not has_audio and best_audio_size and size_bytes:
                size_bytes = size_bytes + best_audio_size

            protocol = str(fmt.get("protocol") or "").lower()
            is_hls = protocol in {"m3u8", "m3u8_native"} or ".m3u8" in media_url.lower()
            is_dash = protocol in {"http_dash_segments", "dash"} or ".mpd" in media_url.lower()

            quality = {
                "label": label,
                "display_label": self._build_display_label(label, is_audio_only, bitrate_bps),
                "selector": selector,
                "format_id": format_id or None,
                "url": media_url,
                "size_bytes": size_bytes,
                "codec": fmt.get("acodec") if is_audio_only else (fmt.get("vcodec") if fmt.get("vcodec") != "none" else fmt.get("acodec")),
                "bitrate": bitrate_bps,
                "hdr": "hdr" in str(fmt.get("dynamic_range") or "").lower(),
                "format": "m3u8" if is_hls else ("mpd" if is_dash else (fmt.get("ext") or self._guess_format(media_url) or "mp4")),
                "has_audio": has_audio,
                "is_hls": is_hls,
            }

            if not is_audio_only and not has_audio and best_audio_url:
                quality["audio_url"] = best_audio_url

            qualities.append(quality)

        if not qualities and info.get("url"):
            single_url = self._clean_url(info.get("url"))
            if single_url:
                single_is_hls = ".m3u8" in single_url.lower()
                single_is_image = self._looks_like_image_url(single_url)
                qualities.append(
                    {
                        "label": "image" if single_is_image else "original",
                        "display_label": "image" if single_is_image else "original",
                        "selector": str(info.get("format_id") or "linkedin_best"),
                        "format_id": str(info.get("format_id")) if info.get("format_id") else None,
                        "url": single_url,
                        "size_bytes": info.get("filesize") or info.get("filesize_approx"),
                        "codec": None,
                        "bitrate": self._to_bps(info.get("tbr") or info.get("abr")),
                        "hdr": False,
                        "format": "jpg" if single_is_image else ("m3u8" if single_is_hls else (info.get("ext") or self._guess_format(single_url) or "mp4")),
                        "is_hls": single_is_hls,
                        "has_audio": False if single_is_image else True,
                    }
                )

        if not qualities:
            thumbnail_url = self._clean_url(info.get("thumbnail"))
            if thumbnail_url:
                qualities.append(
                    {
                        "label": "image",
                        "display_label": "image",
                        "selector": "linkedin_thumbnail",
                        "format_id": None,
                        "url": thumbnail_url,
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "format": "jpg",
                        "is_hls": False,
                        "has_audio": False,
                    }
                )

        if not qualities:
            raise ExtractorError("No downloadable LinkedIn formats available", platform=self.PLATFORM_ID, url=url)

        qualities = self._sort_qualities(qualities)

        subtitles = []
        subtitles.extend(self._parse_subtitles(info.get("subtitles") or {}))
        subtitles.extend(self._parse_subtitles(info.get("automatic_captions") or {}))

        chapters = []
        for chapter in info.get("chapters") or []:
            start = chapter.get("start_time") or 0
            chapters.append({"title": chapter.get("title") or "Chapter", "start_ms": int(start * 1000)})

        manifest_url = None
        for item in qualities:
            if item.get("is_hls"):
                manifest_url = item.get("url")
                break

        headers_required = self._sanitize_headers(info.get("http_headers") or {})
        headers_required.setdefault("Referer", "https://www.linkedin.com/")
        headers_required.setdefault("Origin", "https://www.linkedin.com")
        headers_required.setdefault("User-Agent", self.USER_AGENT_POOL[0])

        return {
            "title": info.get("title") or "LinkedIn Post",
            "author": info.get("uploader") or info.get("channel"),
            "channel_id": info.get("channel_id") or info.get("uploader_id"),
            "thumbnail": self._clean_url(info.get("thumbnail")),
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "description": (info.get("description") or "")[:500] if info.get("description") else None,
            "upload_date": self._normalize_upload_date(info.get("upload_date")),
            "qualities": qualities,
            "subtitles": subtitles,
            "chapters": chapters,
            "is_hls": bool(manifest_url and not any(not item.get("is_hls") for item in qualities)),
            "manifest_url": manifest_url,
            "headers_required": headers_required,
            "content_type": self._infer_content_type(qualities),
        }

    def _build_media_info_from_candidates(self, url: str, html_content: str, candidates: list) -> dict:
        qualities = []
        seen_selectors = set()

        for index, candidate in enumerate(candidates):
            stream_url = self._clean_url(candidate.get("url"))
            if not stream_url:
                continue

            is_image = self._looks_like_image_url(stream_url, candidate.get("content_type"))
            is_hls = ".m3u8" in stream_url.lower() or "mpegurl" in str(candidate.get("content_type") or "")
            format_value = "jpg" if is_image else ("m3u8" if is_hls else ("mpd" if ".mpd" in stream_url.lower() else (self._guess_format(stream_url) or "mp4")))

            label = self._label_from_url(stream_url, is_image=is_image)
            format_id = None
            selector = f"linkedin_{label}_{format_value}_{index}"
            if selector in seen_selectors:
                continue
            seen_selectors.add(selector)

            qualities.append(
                {
                    "label": label,
                    "display_label": label,
                    "selector": selector,
                    "format_id": format_id,
                    "url": stream_url,
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": format_value,
                    "has_audio": False if is_image else True,
                    "is_hls": False if is_image else is_hls,
                }
            )

        if not qualities:
            raise ExtractorError("LinkedIn media not found", platform=self.PLATFORM_ID, url=url)

        qualities = self._sort_qualities(qualities)

        manifest_url = None
        for item in qualities:
            if item.get("is_hls"):
                manifest_url = item.get("url")
                break

        title = self._extract_meta(html_content, "og:title") or self._extract_title(html_content) or "LinkedIn Post"
        thumbnail = self._extract_meta(html_content, "og:image")
        description = self._extract_meta(html_content, "og:description")

        headers_required = {
            "Referer": "https://www.linkedin.com/",
            "Origin": "https://www.linkedin.com",
            "User-Agent": self.USER_AGENT_POOL[0],
        }

        return {
            "title": title,
            "author": None,
            "channel_id": None,
            "thumbnail": thumbnail,
            "duration": None,
            "view_count": None,
            "description": description,
            "upload_date": None,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": bool(manifest_url and not any(not item.get("is_hls") for item in qualities)),
            "manifest_url": manifest_url,
            "headers_required": headers_required,
            "content_type": self._infer_content_type(qualities),
        }

    def _extract_media_candidates_from_html(self, html_content: str) -> list:
        if not html_content:
            return []

        candidates = []
        seen = set()

        def add_candidate(value: str, content_type: str = ""):
            cleaned = self._clean_url(value)
            if not cleaned or cleaned in seen:
                return
            if not self._looks_like_media_candidate(cleaned, content_type):
                return
            seen.add(cleaned)
            candidates.append(
                {
                    "url": cleaned,
                    "content_type": str(content_type or "").lower(),
                }
            )

        for key in ("og:video", "og:video:url", "twitter:player:stream"):
            add_candidate(self._extract_meta(html_content, key), "video/mp4")

        add_candidate(self._extract_meta(html_content, "og:image"), "image/jpeg")
        add_candidate(self._extract_meta(html_content, "twitter:image"), "image/jpeg")

        tag_patterns = [
            r"<video[^>]+src=['\"]([^'\"]+)['\"]",
            r"<source[^>]+src=['\"]([^'\"]+)['\"]",
            r"<img[^>]+src=['\"]([^'\"]+)['\"]",
        ]
        for pattern in tag_patterns:
            for match in re.finditer(pattern, html_content, re.IGNORECASE):
                add_candidate(match.group(1), "image/jpeg" if "<img" in match.group(0).lower() else "")

        key_patterns = [
            r'"(?:progressiveUrl|streamingUrl|playbackUrl|masterPlaylist|masterPlaylistUrl|dashManifestUrl|contentUrl|downloadUrl)"\s*:\s*"([^\"]+)"',
            r'"(?:thumbnailUrl|thumbnail|imageUrl|displayImage|largeImage)"\s*:\s*"([^\"]+)"',
            r"'(?:progressiveUrl|streamingUrl|playbackUrl|masterPlaylist|masterPlaylistUrl|dashManifestUrl|contentUrl|downloadUrl)'\s*:\s*'([^']+)'",
            r"'(?:thumbnailUrl|thumbnail|imageUrl|displayImage|largeImage)'\s*:\s*'([^']+)'",
        ]
        for pattern in key_patterns:
            for match in re.finditer(pattern, html_content, re.IGNORECASE):
                matched_text = match.group(0).lower()
                add_candidate(match.group(1), "image/jpeg" if "image" in matched_text or "thumbnail" in matched_text else "")

        markdown_image_pattern = r"!\[[^\]]*\]\((https?://[^)\s]+)\)"
        for match in re.finditer(markdown_image_pattern, html_content, re.IGNORECASE):
            add_candidate(match.group(1), "image/jpeg")

        url_pattern = r"https?:\\/\\/[^\"'<>\s]+(?:\\.mp4|\\.m3u8|\\.mpd|\\.jpg|\\.jpeg|\\.png|\\.webp|\\.gif|\\.bmp)[^\"'<>\s]*"
        for match in re.finditer(url_pattern, html_content, re.IGNORECASE):
            add_candidate(match.group(0))

        licdn_media_pattern = r"https?:\\/\\/media\\.licdn\\.com\\/[^\"'<>\s]+"
        for match in re.finditer(licdn_media_pattern, html_content, re.IGNORECASE):
            add_candidate(match.group(0), "image/jpeg")

        for script_json in re.finditer(
            r"<script[^>]*type=['\"]application/ld\+json['\"][^>]*>(.*?)</script>",
            html_content,
            re.IGNORECASE | re.DOTALL,
        ):
            payload = script_json.group(1).strip()
            if not payload:
                continue
            try:
                data = json.loads(payload)
            except Exception:
                continue
            for value in self._collect_string_values(data):
                add_candidate(value)

        return candidates

    def _extract_meta(self, html_content: str, property_name: str):
        escaped = re.escape(property_name)
        patterns = [
            rf"<meta[^>]+property=['\"]{escaped}['\"][^>]+content=['\"]([^'\"]+)['\"]",
            rf"<meta[^>]+content=['\"]([^'\"]+)['\"][^>]+property=['\"]{escaped}['\"]",
            rf"<meta[^>]+name=['\"]{escaped}['\"][^>]+content=['\"]([^'\"]+)['\"]",
            rf"<meta[^>]+content=['\"]([^'\"]+)['\"][^>]+name=['\"]{escaped}['\"]",
        ]
        for pattern in patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                return self._clean_url(match.group(1))
        return None

    def _extract_title(self, html_content: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html_content or "", re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        value = unescape(match.group(1)).strip()
        return re.sub(r"\s+", " ", value) or None

    def _clean_url(self, value):
        text = str(value or "").strip().strip("\"'")
        if not text:
            return None

        text = unescape(text)
        text = text.replace("\\/", "/")
        text = text.replace("\\u002F", "/")
        text = text.replace("\\u003A", ":")
        text = text.replace("\\u0026", "&")
        text = text.replace("\\u003D", "=")
        text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda item: chr(int(item.group(1), 16)), text)

        if text.startswith("//"):
            text = f"https:{text}"

        if text.startswith("http%3A") or text.startswith("https%3A"):
            text = unquote(text)

        text = text.strip().strip("\"'")
        if not text.lower().startswith(("http://", "https://")):
            return None

        return text

    def _looks_like_media_candidate(self, value: str, content_type: str = "") -> bool:
        if self._looks_like_video_url(value, content_type):
            return True
        if self._looks_like_image_url(value, content_type):
            return True
        return False

    def _looks_like_video_url(self, value: str, content_type: str = "") -> bool:
        lowered = str(value or "").lower()
        if not lowered or lowered.startswith("blob:"):
            return False

        if any(token in lowered for token in (".mp4", ".m3u8", ".mpd", "format=m3u8", "format=mp4")):
            return True

        if "dms.licdn.com" in lowered and any(token in lowered for token in ("video", "vod", "playlist", "stream", "progressive", "master")):
            return True

        normalized_content_type = str(content_type or "").lower()
        if "video" in normalized_content_type:
            return True
        if "mpegurl" in normalized_content_type:
            return True
        if "dash+xml" in normalized_content_type:
            return True

        return False

    def _looks_like_image_url(self, value: str, content_type: str = "") -> bool:
        lowered = str(value or "").lower()
        if not lowered or lowered.startswith("blob:"):
            return False

        if any(token in lowered for token in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")):
            return True

        if "media.licdn.com" in lowered and "/dms/image/" in lowered:
            return True

        normalized_content_type = str(content_type or "").lower()
        if normalized_content_type.startswith("image/"):
            return True

        return False

    def _collect_string_values(self, value):
        values = []

        def walk(node):
            if isinstance(node, dict):
                for child in node.values():
                    walk(child)
                return
            if isinstance(node, list):
                for child in node:
                    walk(child)
                return
            if isinstance(node, str):
                values.append(node)

        walk(value)
        return values

    def _label_from_url(self, stream_url: str, is_image: bool = False) -> str:
        if is_image:
            return "image"

        value = str(stream_url or "").lower()
        match = re.search(r"(?<!\d)(240|360|480|540|720|1080|1440|2160)p(?!\d)", value)
        if match:
            return f"{match.group(1)}p"

        match = re.search(r"(?<!\d)(240|360|480|540|720|1080|1440|2160)(?!\d)", value)
        if match:
            return f"{match.group(1)}p"

        if ".m3u8" in value:
            return "hls"
        return "original"

    def _guess_format(self, stream_url: str) -> str:
        value = str(stream_url or "").split("?", 1)[0].lower()
        for ext in ("m3u8", "mpd", "mp4", "m4v", "mov", "webm"):
            if value.endswith(f".{ext}"):
                return ext
        return "mp4"

    def _sort_qualities(self, qualities: list) -> list:
        def sort_key(quality: dict):
            label = str(quality.get("label") or "").lower()
            is_audio = label in {"audio", "audio_only", "audio only"}
            is_image = str(quality.get("format") or "").lower() in {"jpg", "jpeg", "png", "webp", "gif", "bmp"}
            is_hls = bool(quality.get("is_hls"))
            height_match = re.search(r"(\d{3,4})p", label)
            height = int(height_match.group(1)) if height_match else 0
            bitrate = quality.get("bitrate") or 0
            return (2 if is_audio else (1 if is_image else 0), 1 if is_hls else 0, -height, -int(bitrate))

        return sorted(qualities, key=sort_key)

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

    def _label_for_height(self, height):
        try:
            value = int(height)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return f"{value}p"

    def _label_from_note(self, value) -> str:
        text = str(value or "").strip().lower()
        match = re.search(r"(\d{3,4})p", text)
        if match:
            return f"{match.group(1)}p"
        if "audio" in text:
            return "audio_only"
        return None

    def _to_bps(self, value):
        if value is None:
            return None
        try:
            return int(float(value) * 1000)
        except (TypeError, ValueError):
            return None

    def _build_display_label(self, label: str, is_audio_only: bool, bitrate_bps):
        if is_audio_only:
            if bitrate_bps:
                return f"audio {int(round(bitrate_bps / 1000))}kbps"
            return "audio"
        return str(label or "original")

    def _parse_subtitles(self, subtitles_data: dict) -> list:
        items = []
        for lang, entries in subtitles_data.items():
            for item in entries or []:
                subtitle_url = self._clean_url(item.get("url"))
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

    def _sanitize_headers(self, headers: dict) -> dict:
        clean_headers = {}
        for key, value in (headers or {}).items():
            if key is None or value is None:
                continue
            key_text = str(key).strip()
            value_text = str(value).strip()
            if not key_text or not value_text:
                continue
            clean_headers[key_text] = value_text
        return clean_headers

    def _is_profile_or_company_url(self, url: str) -> bool:
        value = str(url or "").lower()
        return "linkedin.com/in/" in value or "linkedin.com/company/" in value

    def _pick_profile_image_url(self, candidates: list) -> str:
        valid = []
        for item in candidates:
            cleaned = self._clean_url(item)
            if not cleaned:
                continue
            if not self._looks_like_image_url(cleaned):
                continue
            valid.append(cleaned)

        if not valid:
            return None

        preferred = [
            item
            for item in valid
            if "media.licdn.com" in item.lower()
            and ("/dms/image/" in item.lower() or "profile-displayphoto" in item.lower())
        ]
        if preferred:
            return preferred[0]

        return valid[0]

    def _extract_urn_ids(self, url: str) -> list:
        value = str(url or "")
        candidates = []
        seen = set()

        for match in re.findall(r"(?:share|activity)[-:](\d{10,30})", value, re.IGNORECASE):
            entry = ("share", match)
            if entry not in seen:
                seen.add(entry)
                candidates.append(entry)
            entry = ("activity", match)
            if entry not in seen:
                seen.add(entry)
                candidates.append(entry)

        for urn_type, urn_id in re.findall(r"urn:li:(share|activity):(\d{10,30})", value, re.IGNORECASE):
            entry = (urn_type.lower(), urn_id)
            if entry not in seen:
                seen.add(entry)
                candidates.append(entry)

        return candidates

    def _infer_content_type(self, qualities: list) -> str:
        if not qualities:
            return "video"
        image_formats = {"jpg", "jpeg", "png", "webp", "gif", "bmp"}
        if all(str(item.get("format") or "").lower() in image_formats for item in qualities if isinstance(item, dict)):
            return "image"
        return "video"

    def _is_valid_media_info(self, media_info) -> bool:
        if not isinstance(media_info, dict):
            return False
        title = str(media_info.get("title") or "").strip()
        qualities = media_info.get("qualities") or []
        if not title or not isinstance(qualities, list):
            return False
        for item in qualities:
            if not isinstance(item, dict):
                continue
            if str(item.get("url") or "").strip():
                return True
        return False
