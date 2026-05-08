import json
import asyncio
import logging
import re
import time
from datetime import datetime
from html import unescape as html_unescape
from urllib.parse import unquote, urlparse

import yt_dlp
from playwright.sync_api import sync_playwright

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class InstagramExtractor(BaseExtractor):
    PLATFORM_ID = "instagram"
    REQUIRES_HEADLESS = True
    REQUIRES_PROXY = True
    TEST_URL = "https://www.instagram.com/p/CnYB4JTDaaG/"

    TERMINAL_ERROR_MARKERS = (
        "private",
        "not available",
        "login",
        "unsupported url",
        "status code 404",
    )

    PROFILE_RESERVED_SEGMENTS = {
        "p",
        "reel",
        "reels",
        "stories",
        "tv",
        "explore",
        "accounts",
        "about",
        "developer",
        "legal",
        "privacy",
        "terms",
        "direct",
        "challenge",
        "api",
    }

    def extract(self, url: str) -> dict:
        source_url = self._resolve_source_url(url)
        last_error = None
        attempts = (
            ("yt-dlp", self._extract_with_yt_dlp),
            ("playwright", self._extract_with_playwright),
        )

        for source_name, extractor in attempts:
            try:
                media_info = extractor(source_url)
                if not self._is_valid_media_info(media_info):
                    raise ExtractorError("Extractor returned invalid media metadata", platform=self.PLATFORM_ID, url=url)
                media_info.setdefault("platform", self.PLATFORM_ID)
                media_info.setdefault("content_type", self._infer_content_type(media_info))
                return media_info
            except Exception as exc:
                last_error = exc
                logger.warning("Instagram %s extraction failed for %s: %s", source_name, source_url, exc)
                if self._is_terminal_error(str(exc)):
                    break

        if isinstance(last_error, ExtractorError):
            raise last_error
        if last_error is not None:
            raise ExtractorError("Failed to extract Instagram media", platform=self.PLATFORM_ID, url=url) from last_error
        raise ExtractorError("Failed to extract Instagram media", platform=self.PLATFORM_ID, url=url)

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
                "Referer": "https://www.instagram.com/",
            },
        }

        proxy_url = self._resolve_proxy_url()
        if proxy_url:
            options["proxy"] = proxy_url

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise ExtractorError(self._friendly_download_error(str(exc)), platform=self.PLATFORM_ID, url=url) from exc

        return self._build_from_yt_dlp_info(info, url)

    def _resolve_source_url(self, url: str) -> str:
        normalized = self._normalize_url(url) or str(url or "").strip()
        if not normalized:
            return url
        if not self._looks_like_profile_url(normalized):
            return normalized

        resolved = self._resolve_profile_media_url(normalized)
        return resolved or normalized

    def _looks_like_profile_url(self, url: str) -> bool:
        parsed = urlparse(str(url or "").strip())
        hostname = (parsed.hostname or "").lower()
        if hostname not in {"instagram.com", "www.instagram.com"}:
            return False

        segments = [segment for segment in (parsed.path or "").split("/") if segment]
        if len(segments) != 1:
            return False

        first = segments[0].lower()
        if first in self.PROFILE_RESERVED_SEGMENTS:
            return False
        return True

    def _resolve_profile_media_url(self, profile_url: str) -> str:
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "noplaylist": False,
            "playlistend": 1,
            "socket_timeout": 20,
            "retries": 1,
            "http_headers": {
                "User-Agent": self.USER_AGENT_POOL[0],
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.instagram.com/",
            },
        }

        proxy_url = self._resolve_proxy_url()
        if proxy_url:
            options["proxy"] = proxy_url

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(profile_url, download=False)
        except Exception:
            info = None

        extracted = self._extract_first_media_url_from_profile_info(info)
        if extracted:
            return extracted

        return self._resolve_profile_media_url_from_html(profile_url)

    def _extract_first_media_url_from_profile_info(self, info: dict) -> str:
        if not isinstance(info, dict):
            return None

        entries = info.get("entries") or []
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            for key in ("webpage_url", "url", "original_url"):
                value = self._normalize_url(entry.get(key))
                if value and "instagram.com" in value.lower():
                    return value

            entry_id = str(entry.get("id") or "").strip()
            if entry_id:
                reel_candidate = self._normalize_url(f"https://www.instagram.com/reel/{entry_id}/")
                if reel_candidate:
                    return reel_candidate

                post_candidate = self._normalize_url(f"https://www.instagram.com/p/{entry_id}/")
                if post_candidate:
                    return post_candidate

        return None

    def _resolve_profile_media_url_from_html(self, profile_url: str) -> str:
        try:
            response = self.http_get(profile_url)
        except Exception:
            return None

        html = response.text or ""
        patterns = [
            r'"(/reel/[A-Za-z0-9_-]+/)"',
            r'"(/p/[A-Za-z0-9_-]+/)"',
            r'href="(/reel/[A-Za-z0-9_-]+/)"',
            r'href="(/p/[A-Za-z0-9_-]+/)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                candidate = f"https://www.instagram.com{match.group(1)}"
                normalized = self._normalize_url(candidate)
                if normalized:
                    return normalized

        return None

    def _build_from_yt_dlp_info(self, info: dict, url: str) -> dict:
        root = self._resolve_primary_info(info)
        if not isinstance(root, dict):
            raise ExtractorError("Instagram extractor returned empty metadata", platform=self.PLATFORM_ID, url=url)

        formats = root.get("formats") or []
        qualities = []
        seen = set()

        best_audio = None
        best_audio_bitrate = 0
        for fmt in formats:
            stream_url = self._normalize_url(fmt.get("url"))
            if not stream_url:
                continue
            vcodec = str(fmt.get("vcodec") or "").lower()
            acodec = str(fmt.get("acodec") or "").lower()
            is_audio_only = vcodec == "none" and acodec not in {"", "none"}
            if not is_audio_only:
                continue
            bitrate = fmt.get("abr") or fmt.get("tbr") or 0
            if bitrate > best_audio_bitrate:
                best_audio_bitrate = bitrate
                best_audio = fmt

        best_audio_url = self._normalize_url((best_audio or {}).get("url")) if best_audio else None
        best_audio_size = None
        if best_audio:
            best_audio_size = best_audio.get("filesize") or best_audio.get("filesize_approx")

        for index, fmt in enumerate(formats):
            stream_url = self._normalize_url(fmt.get("url"))
            if not stream_url:
                continue

            vcodec = str(fmt.get("vcodec") or "").lower()
            acodec = str(fmt.get("acodec") or "").lower()
            has_video = bool(vcodec and vcodec != "none")
            has_audio = bool(acodec and acodec != "none")
            is_audio_only = not has_video and has_audio

            if not has_video and not is_audio_only:
                continue

            format_ext = str(fmt.get("ext") or "").strip().lower() or self._guess_ext_from_url(stream_url)
            if not format_ext:
                format_ext = "m4a" if is_audio_only else "mp4"

            label = "audio_only" if is_audio_only else self._label_for_width(fmt.get("width"))
            if not label and not is_audio_only:
                label = self._label_for_width(fmt.get("height"))
            if not label:
                label = "original"

            format_id = str(fmt.get("format_id") or "").strip()
            selector = format_id or f"ig_{label}_{format_ext}_{index}"
            if selector in seen:
                continue
            seen.add(selector)

            bitrate_bps = self._to_bps(fmt.get("tbr") or fmt.get("abr"))
            fps_value = self._to_int(fmt.get("fps"))
            size_bytes = fmt.get("filesize") or fmt.get("filesize_approx")
            if has_video and not has_audio and size_bytes and best_audio_size:
                size_bytes = size_bytes + best_audio_size

            protocol = str(fmt.get("protocol") or "").lower()
            is_hls = protocol in {"m3u8", "m3u8_native"} or ".m3u8" in stream_url.lower()

            quality = {
                "label": label,
                "display_label": "",
                "selector": selector,
                "format_id": format_id or None,
                "url": stream_url,
                "size_bytes": size_bytes,
                "codec": acodec if is_audio_only else (vcodec if has_video else acodec),
                "bitrate": bitrate_bps,
                "hdr": "hdr" in str(fmt.get("dynamic_range") or fmt.get("format_note") or "").lower(),
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
            fallback_url = self._normalize_url(root.get("url"))
            if fallback_url:
                fallback_is_hls = ".m3u8" in fallback_url.lower()
                fallback_is_image = self._looks_like_image_url(fallback_url)
                fallback_format = self._guess_ext_from_url(fallback_url) or ("jpg" if fallback_is_image else "mp4")
                qualities.append(
                    {
                        "label": "image" if fallback_is_image else "original",
                        "display_label": "Image" if fallback_is_image else "Original",
                        "selector": "ig_fallback",
                        "format_id": None,
                        "url": fallback_url,
                        "size_bytes": root.get("filesize") or root.get("filesize_approx"),
                        "codec": None,
                        "bitrate": self._to_bps(root.get("tbr") or root.get("abr")),
                        "hdr": False,
                        "fps": None,
                        "format": "m3u8" if fallback_is_hls else fallback_format,
                        "has_audio": not fallback_is_image,
                        "is_hls": fallback_is_hls,
                    }
                )

        if not qualities:
            thumbnail_url = self._normalize_url(root.get("thumbnail"))
            if thumbnail_url:
                qualities.append(
                    {
                        "label": "image",
                        "display_label": "Image",
                        "selector": "ig_thumbnail",
                        "format_id": None,
                        "url": thumbnail_url,
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "fps": None,
                        "format": "jpg",
                        "has_audio": False,
                        "is_hls": False,
                    }
                )

        if not qualities:
            raise ExtractorError("Instagram media stream not found", platform=self.PLATFORM_ID, url=url)

        qualities = self._sort_qualities(qualities)

        subtitles = []
        subtitles.extend(self._parse_subtitles(root.get("subtitles") or {}))
        subtitles.extend(self._parse_subtitles(root.get("automatic_captions") or {}))

        chapters = []
        for chapter in root.get("chapters") or []:
            start_time = chapter.get("start_time") or 0
            chapters.append({"title": chapter.get("title") or "Chapter", "start_ms": int(float(start_time) * 1000)})

        thumbnails = root.get("thumbnails") or []
        thumbnail = self._normalize_url(root.get("thumbnail"))
        if not thumbnail and thumbnails:
            thumbnail = self._normalize_url(thumbnails[0].get("url"))

        manifest_url = None
        for quality in qualities:
            if quality.get("is_hls"):
                manifest_url = quality.get("url")
                break

        headers_required = root.get("http_headers") or {}
        if not headers_required:
            headers_required = {
                "User-Agent": self.USER_AGENT_POOL[0],
                "Referer": "https://www.instagram.com/",
            }

        return {
            "title": root.get("title") or root.get("fulltitle") or "Instagram Post",
            "author": root.get("uploader") or root.get("channel") or root.get("uploader_id"),
            "channel_id": root.get("channel_id") or root.get("uploader_id"),
            "thumbnail": thumbnail,
            "duration": root.get("duration"),
            "view_count": root.get("view_count") or root.get("like_count"),
            "description": (root.get("description") or "")[:500] if root.get("description") else None,
            "upload_date": self._normalize_upload_date(root.get("upload_date") or root.get("timestamp")),
            "qualities": qualities,
            "subtitles": subtitles,
            "chapters": chapters,
            "is_hls": bool(manifest_url and not any(not item.get("is_hls") for item in qualities if item.get("url"))),
            "manifest_url": manifest_url,
            "headers_required": headers_required,
        }

    def _extract_with_playwright(self, url: str) -> dict:
        media = None

        def handle_response(response):
            nonlocal media
            if media is not None:
                return
            targets = [
                "/api/v1/media/",
                "graphql/query",
                "/api/graphql",
                "xdt_api__v1__media__shortcode__web_info",
                "?__a=1",
            ]
            if not any(target in response.url for target in targets):
                return
            try:
                data = response.json()
            except (asyncio.CancelledError, Exception):
                return
            media = self._extract_media(data)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = None
                try:
                    context = browser.new_context(user_agent=self.USER_AGENT_POOL[0])
                    page = context.new_page()
                    page.on("response", handle_response)
                    page.goto(url, wait_until="networkidle", timeout=30000)

                    if "login" in page.url:
                        raise ExtractorError(
                            "This content requires Instagram login. Only public content can be downloaded.",
                            platform=self.PLATFORM_ID,
                            url=url,
                        )

                    deadline = time.time() + 12
                    while media is None and time.time() < deadline:
                        time.sleep(0.2)

                    if media is not None:
                        return self._build_media_info(media)

                    html_content = page.content()
                    media = self._extract_media_from_html(html_content)
                    if media is not None:
                        return self._build_media_info(media)

                    meta_media_info = self._build_from_html_meta(html_content)
                    if meta_media_info is not None:
                        return meta_media_info

                    raise ExtractorError("Could not extract Instagram media", platform=self.PLATFORM_ID, url=url)
                finally:
                    try:
                        if page is not None:
                            page.off("response", handle_response)
                    except Exception:
                        pass
                    try:
                        browser.close()
                    except Exception:
                        logger.debug("Ignoring browser close error in instagram extractor", exc_info=True)
        except ExtractorError:
            raise
        except Exception as exc:
            raise ExtractorError("Failed to extract Instagram media", platform=self.PLATFORM_ID, url=url) from exc

    def _resolve_primary_info(self, info):
        if not isinstance(info, dict):
            return None
        if info.get("_type") != "playlist":
            return info
        entries = info.get("entries") or []
        for entry in entries:
            if isinstance(entry, dict):
                return entry
        return None

    def _extract_media(self, data):
        if not isinstance(data, dict):
            return None

        if "items" in data and data.get("items"):
            return data["items"][0]

        graphql = data.get("data") or data.get("graphql")
        if isinstance(graphql, dict):
            if "shortcode_media" in graphql:
                return graphql.get("shortcode_media")
            if "xdt_shortcode_media" in graphql:
                return graphql.get("xdt_shortcode_media")

            media_info = graphql.get("xdt_api__v1__media__shortcode__web_info")
            if isinstance(media_info, dict):
                items = media_info.get("items") or []
                if items and isinstance(items[0], dict):
                    return items[0]

        media_info = data.get("xdt_api__v1__media__shortcode__web_info")
        if isinstance(media_info, dict):
            items = media_info.get("items") or []
            if items and isinstance(items[0], dict):
                return items[0]

        if isinstance(data.get("media"), dict):
            return data.get("media")

        return None

    def _extract_media_from_html(self, html: str):
        if not html:
            return None

        for marker in ("\"shortcode_media\":", "\"xdt_shortcode_media\":"):
            obj = self._extract_json_object_after_marker(html, marker)
            if isinstance(obj, dict):
                return obj

        shared_data = self._extract_json_object_after_marker(html, "window._sharedData=")
        if isinstance(shared_data, dict):
            post_page = ((shared_data.get("entry_data") or {}).get("PostPage") or [])
            if post_page and isinstance(post_page[0], dict):
                media = ((post_page[0].get("graphql") or {}).get("shortcode_media"))
                if isinstance(media, dict):
                    return media

        return None

    def _build_media_info(self, media):
        title = "Instagram Post"
        caption = None
        author = None
        thumbnail = None
        qualities = []
        subtitles = []
        chapters = []

        if "caption" in media and media.get("caption"):
            caption = media.get("caption", {}).get("text")
        if "edge_media_to_caption" in media:
            edges = media.get("edge_media_to_caption", {}).get("edges") or []
            if edges:
                caption = edges[0].get("node", {}).get("text")
        if caption:
            title = caption[:200]

        author = (media.get("user") or {}).get("username") or (media.get("owner") or {}).get("username")

        media_type = media.get("media_type")
        is_video = media.get("is_video") or media_type == 2
        is_carousel = media_type == 8 or "carousel_media" in media or "edge_sidecar_to_children" in media

        if is_carousel:
            items = media.get("carousel_media")
            if not items:
                edges = media.get("edge_sidecar_to_children", {}).get("edges") or []
                items = [edge.get("node") for edge in edges if edge.get("node")]
            for index, item in enumerate(items or [], start=1):
                item_is_video = item.get("media_type") == 2 or item.get("is_video")
                if item_is_video:
                    for video in item.get("video_versions") or []:
                        qualities.append(self._video_quality(video, index))
                else:
                    image = (item.get("image_versions2") or {}).get("candidates") or []
                    if image:
                        qualities.append(self._image_quality(image[0], index))
            if qualities:
                thumbnail = qualities[0].get("url")
        elif is_video:
            videos = media.get("video_versions") or []
            for video in videos:
                qualities.append(self._video_quality(video))
            if videos:
                thumbnail = (media.get("image_versions2") or {}).get("candidates", [{}])[0].get("url")
        else:
            images = (media.get("image_versions2") or {}).get("candidates") or []
            if images:
                qualities.append(self._image_quality(images[0]))
                thumbnail = images[0].get("url")

        if not thumbnail:
            thumbnail = media.get("display_url")

        qualities = [q for q in qualities if q and q.get("url")]

        if not qualities:
            raise ExtractorError("Could not extract Instagram media streams", platform=self.PLATFORM_ID)

        return {
            "title": title,
            "author": author,
            "channel_id": None,
            "thumbnail": thumbnail,
            "duration": media.get("video_duration") or media.get("duration"),
            "view_count": media.get("view_count") or media.get("like_count"),
            "description": (caption or "")[:500] if caption else None,
            "upload_date": None,
            "qualities": self._sort_qualities(qualities),
            "subtitles": subtitles,
            "chapters": chapters,
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {
                "User-Agent": self.USER_AGENT_POOL[0],
                "Referer": "https://www.instagram.com/",
            },
        }

    def _build_from_html_meta(self, html: str):
        if not html:
            return None

        title = self._extract_meta(html, "og:title") or "Instagram Post"
        description = self._extract_meta(html, "og:description")
        thumbnail = self._extract_meta(html, "og:image") or self._extract_meta(html, "twitter:image")
        video_url = self._extract_meta(html, "og:video:secure_url") or self._extract_meta(html, "og:video")

        if not video_url:
            url_match = re.search(r'"contentUrl"\s*:\s*"([^"]+)"', html)
            if url_match:
                video_url = html_unescape(unquote(url_match.group(1).replace("\\u0026", "&").replace("\\/", "/")))

        video_url = self._normalize_url(video_url)
        thumbnail = self._normalize_url(thumbnail)

        qualities = []
        if video_url:
            format_ext = self._guess_ext_from_url(video_url) or "mp4"
            is_hls = ".m3u8" in video_url.lower()
            qualities.append(
                {
                    "label": "original",
                    "display_label": "Original",
                    "selector": "ig_meta_video",
                    "format_id": None,
                    "url": video_url,
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "fps": None,
                    "format": "m3u8" if is_hls else format_ext,
                    "has_audio": True,
                    "is_hls": is_hls,
                }
            )
        elif thumbnail:
            qualities.append(
                {
                    "label": "image",
                    "display_label": "Image",
                    "selector": "ig_meta_image",
                    "format_id": None,
                    "url": thumbnail,
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "fps": None,
                    "format": "jpg",
                    "has_audio": False,
                    "is_hls": False,
                }
            )

        if not qualities:
            return None

        manifest_url = None
        for quality in qualities:
            if quality.get("is_hls"):
                manifest_url = quality.get("url")
                break

        return {
            "title": title,
            "author": None,
            "channel_id": None,
            "thumbnail": thumbnail,
            "duration": None,
            "view_count": None,
            "description": (description or "")[:500] if description else None,
            "upload_date": None,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": bool(manifest_url),
            "manifest_url": manifest_url,
            "headers_required": {
                "User-Agent": self.USER_AGENT_POOL[0],
                "Referer": "https://www.instagram.com/",
            },
        }

    def _extract_json_object_after_marker(self, text: str, marker: str):
        if not text or not marker:
            return None

        search_index = 0
        while True:
            marker_index = text.find(marker, search_index)
            if marker_index < 0:
                return None
            brace_index = text.find("{", marker_index + len(marker))
            if brace_index < 0:
                return None

            payload = self._extract_balanced_json_object(text, brace_index)
            if payload:
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    pass

            search_index = marker_index + len(marker)

    def _extract_balanced_json_object(self, text: str, start_index: int):
        if start_index < 0 or start_index >= len(text) or text[start_index] != "{":
            return None

        depth = 0
        in_string = False
        escaped = False

        for index in range(start_index, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue

            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start_index : index + 1]

        return None

    def _extract_meta(self, html: str, property_name: str):
        escaped = re.escape(property_name)
        patterns = [
            rf'<meta[^>]+property="{escaped}"[^>]+content="([^"]+)"',
            rf'<meta[^>]+content="([^"]+)"[^>]+property="{escaped}"',
            rf'<meta[^>]+name="{escaped}"[^>]+content="([^"]+)"',
            rf'<meta[^>]+content="([^"]+)"[^>]+name="{escaped}"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                return html_unescape(unquote(match.group(1).replace("\\u0026", "&").replace("\\/", "/")))
        return None

    def _parse_subtitles(self, subtitles_data: dict) -> list:
        items = []
        for lang, entries in subtitles_data.items():
            for entry in entries or []:
                subtitle_url = self._normalize_url(entry.get("url"))
                if not subtitle_url:
                    continue
                items.append(
                    {
                        "lang": lang,
                        "label": lang,
                        "url": subtitle_url,
                        "format": entry.get("ext") or "vtt",
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

        if re.fullmatch(r"\d{10,13}", raw):
            try:
                timestamp = int(raw)
                if len(raw) == 13:
                    timestamp = int(timestamp / 1000)
                return datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OSError):
                return None

        return None

    def _video_quality(self, video, index=None):
        width = video.get("width") or video.get("height")
        label = self._label_for_width(width)
        if index:
            label = f"{label}_item_{index}"
        url = self._normalize_url(video.get("url"))
        if not url:
            return None
        selector = f"ig_{label}_{index or 'single'}"
        is_hls = ".m3u8" in url.lower()
        return {
            "label": label,
            "display_label": self._build_display_label({"label": label, "format": "m3u8" if is_hls else "mp4"}, False),
            "selector": selector,
            "format_id": None,
            "url": url,
            "size_bytes": video.get("file_size"),
            "codec": None,
            "bitrate": video.get("bitrate"),
            "hdr": False,
            "fps": self._to_int(video.get("fps")),
            "format": "m3u8" if is_hls else "mp4",
            "has_audio": True,
            "is_hls": is_hls,
        }

    def _image_quality(self, image, index=None):
        width = image.get("width")
        label = f"{width}px" if width else "original"
        if index:
            label = f"{label}_item_{index}"
        url = self._normalize_url(image.get("url"))
        if not url:
            return None
        return {
            "label": label,
            "display_label": "Image",
            "selector": f"ig_{label}_{index or 'single'}",
            "format_id": None,
            "url": url,
            "size_bytes": None,
            "codec": None,
            "bitrate": None,
            "hdr": False,
            "fps": None,
            "format": "jpg",
            "has_audio": False,
            "is_hls": False,
        }

    def _sort_qualities(self, qualities: list) -> list:
        return sorted(qualities, key=self._quality_sort_key)

    def _quality_sort_key(self, quality: dict):
        label = str(quality.get("label") or "").lower()
        is_audio = label in {"audio", "audio_only", "audio only"}
        height_match = re.search(r"(\d{3,4})p", label)
        height = int(height_match.group(1)) if height_match else 0
        bitrate = quality.get("bitrate") or 0
        return (1 if is_audio else 0, -height, -int(bitrate))

    def _build_display_label(self, quality: dict, is_audio_only: bool) -> str:
        if is_audio_only:
            return "Audio"

        label = str(quality.get("label") or "").strip()
        fps = quality.get("fps")
        codec = str(quality.get("codec") or "").strip().lower()
        hdr = bool(quality.get("hdr"))

        pieces = [label or "Original"]
        if fps:
            pieces.append(f"{fps}fps")
        if codec and codec not in {"none", "unknown"}:
            pieces.append(codec.upper())
        if hdr:
            pieces.append("HDR")

        return " ".join(pieces)

    def _label_for_width(self, width):
        value = self._to_int(width)
        if not value:
            return "original"
        if value >= 3840:
            return "4K"
        if value >= 1920:
            return "1080p"
        if value >= 1280:
            return "720p"
        if value >= 854:
            return "480p"
        return "360p"

    def _to_int(self, value):
        if value is None:
            return None
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None

    def _to_bps(self, value):
        if value is None:
            return None
        try:
            return int(float(value) * 1000)
        except (TypeError, ValueError):
            return None

    def _guess_ext_from_url(self, media_url: str):
        value = str(media_url or "").strip()
        if not value:
            return None
        parsed = urlparse(value)
        path = parsed.path or ""
        if "." not in path:
            return None
        extension = path.rsplit(".", 1)[-1].lower()
        return extension or None

    def _normalize_url(self, value):
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.startswith("//"):
            candidate = f"https:{candidate}"
        candidate = html_unescape(candidate.replace("\\u0026", "&").replace("\\/", "/"))
        if not candidate.lower().startswith(("http://", "https://")):
            return None
        return candidate

    def _looks_like_image_url(self, value: str) -> bool:
        candidate = str(value or "").split("?", 1)[0].lower()
        return candidate.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"))

    def _is_valid_media_info(self, media_info: dict) -> bool:
        if not isinstance(media_info, dict):
            return False

        title = media_info.get("title")
        qualities = media_info.get("qualities")
        if not title or not isinstance(title, str):
            return False
        if not isinstance(qualities, list) or not qualities:
            return False

        valid_quality_count = 0
        for quality in qualities:
            if not isinstance(quality, dict):
                continue
            if quality.get("url"):
                valid_quality_count += 1
        return valid_quality_count > 0

    def _is_terminal_error(self, message: str) -> bool:
        lowered = (message or "").lower()
        if not lowered:
            return False
        return any(marker in lowered for marker in self.TERMINAL_ERROR_MARKERS)

    def _friendly_download_error(self, message: str) -> str:
        lowered = (message or "").lower()
        if "login" in lowered or "private" in lowered:
            return "This Instagram content is private or requires login."
        if "not available" in lowered or "404" in lowered:
            return "This Instagram content is no longer available."
        return message or "Failed to extract Instagram media"

    def _infer_content_type(self, media_info: dict) -> str:
        qualities = media_info.get("qualities") or []
        if not qualities:
            return "video"

        image_like = 0
        audio_like = 0
        for quality in qualities:
            label = str((quality or {}).get("label") or "").strip().lower()
            format_value = str((quality or {}).get("format") or "").strip().lower()
            url_value = str((quality or {}).get("url") or "")

            if label in {"audio", "audio_only", "audio only"}:
                audio_like += 1
                continue
            if format_value in {"jpg", "jpeg", "png", "gif", "webp", "bmp"}:
                image_like += 1
                continue
            if self._looks_like_image_url(url_value):
                image_like += 1

        if image_like == len(qualities):
            return "image"
        if audio_like == len(qualities):
            return "audio"
        return "video"
