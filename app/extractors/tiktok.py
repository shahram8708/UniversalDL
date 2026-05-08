import copy
import asyncio
import logging
import os
import re
import time
from datetime import datetime
from urllib.parse import urlparse

import yt_dlp
from playwright.sync_api import sync_playwright

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class TikTokExtractor(BaseExtractor):
    PLATFORM_ID = "tiktok"
    REQUIRES_HEADLESS = True
    REQUIRES_PROXY = False
    TEST_URL = "https://www.tiktok.com/@tiktok/video/6584647400055377158"

    PUBLIC_API_ENDPOINTS = (
        "https://www.tikwm.com/api/",
        "https://tikwm.com/api/",
    )

    _TERMINAL_ERROR_MARKERS = (
        "private",
        "not available",
        "forbidden",
        "login",
        "status code 10204",
        "status code 10216",
    )

    _RETRYABLE_ERROR_MARKERS = (
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "connection refused",
        "network is unreachable",
        "service unavailable",
        "too many requests",
        "429",
    )

    def extract(self, url: str) -> dict:
        last_error = None
        attempts = (
            ("yt-dlp", self._extract_with_yt_dlp),
            ("playwright", self._extract_with_playwright),
            ("public-api", self._extract_with_public_api),
        )

        for source_name, extractor in attempts:
            try:
                media_info = extractor(url)
                if not self._is_valid_media_info(media_info):
                    raise ExtractorError("Extractor returned invalid media metadata", platform=self.PLATFORM_ID, url=url)
                media_info.setdefault("platform", self.PLATFORM_ID)
                return media_info
            except Exception as exc:
                last_error = exc
                logger.warning("TikTok %s extraction failed for %s: %s", source_name, url, exc)
                if self._is_terminal_error(str(exc)):
                    break

        if isinstance(last_error, ExtractorError):
            raise last_error
        if last_error is not None:
            raise ExtractorError("Failed to extract TikTok media", platform=self.PLATFORM_ID, url=url) from last_error
        raise ExtractorError("Failed to extract TikTok media", platform=self.PLATFORM_ID, url=url)

    def _extract_with_playwright(self, url: str) -> dict:
        aweme = None

        def handle_response(response):
            nonlocal aweme
            if aweme is not None:
                return
            target_paths = [
                "api.tiktok.com/api/item/detail",
                "api-h2.tiktok.com/aweme/v1",
                "m.tiktok.com/api/item/detail",
            ]
            if not any(path in response.url for path in target_paths):
                return
            try:
                data = response.json()
            except Exception:
                return
            aweme = self._extract_aweme(data)

        user_agent = next(
            (ua for ua in self.USER_AGENT_POOL if "Android" in ua or "iPhone" in ua),
            self.USER_AGENT_POOL[0],
        )

        browser = None
        try:
            self._ensure_windows_proactor_policy()
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=user_agent)
                page = context.new_page()
                page.on("response", handle_response)
                page.goto(url, wait_until="networkidle", timeout=30000)

                deadline = time.time() + 12
                while aweme is None and time.time() < deadline:
                    time.sleep(0.2)

                if aweme is None:
                    raise ExtractorError("Could not extract TikTok media data", platform=self.PLATFORM_ID, url=url)

                video = aweme.get("video") or {}
                stats = aweme.get("statistics") or {}
                author = aweme.get("author") or {}

                video_url = self._pick_video_url(video)
                audio_url = self._pick_audio_url(aweme)

                qualities = []
                if video_url:
                    qualities.append(
                        {
                            "label": "original",
                            "display_label": "original",
                            "selector": "tiktok_playwright_original",
                            "format_id": None,
                            "url": video_url,
                            "size_bytes": None,
                            "codec": None,
                            "bitrate": None,
                            "hdr": False,
                            "has_audio": True,
                            "format": "mp4",
                        }
                    )
                if audio_url:
                    qualities.append(
                        {
                            "label": "audio_only",
                            "display_label": "audio_only",
                            "selector": "tiktok_playwright_audio",
                            "format_id": None,
                            "url": audio_url,
                            "size_bytes": None,
                            "codec": "aac",
                            "bitrate": None,
                            "hdr": False,
                            "has_audio": True,
                            "format": "m4a",
                        }
                    )

                return {
                    "title": aweme.get("desc") or "TikTok Video",
                    "author": author.get("nickname"),
                    "channel_id": None,
                    "thumbnail": (video.get("cover") or {}).get("url_list", [None])[0],
                    "duration": video.get("duration"),
                    "view_count": stats.get("play_count"),
                    "description": (aweme.get("desc") or "")[:500] if aweme.get("desc") else None,
                    "upload_date": None,
                    "qualities": qualities,
                    "subtitles": [],
                    "chapters": [],
                    "is_hls": False,
                    "manifest_url": None,
                    "headers_required": {"Referer": "https://www.tiktok.com/"},
                }
        except ExtractorError:
            raise
        except Exception as exc:
            raise ExtractorError("Failed to extract TikTok media", platform=self.PLATFORM_ID, url=url) from exc
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    logger.debug("Ignoring browser close error in tiktok extractor", exc_info=True)

    def _extract_with_yt_dlp(self, url: str) -> dict:
        last_error = None

        for ydl_opts in self._build_yt_dlp_attempt_options():
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                return self._build_from_yt_dlp_info(info, url)
            except yt_dlp.utils.DownloadError as exc:
                last_error = exc
                if self._is_terminal_error(str(exc)):
                    break
                continue

        if last_error is not None:
            raise ExtractorError(self._friendly_download_error(str(last_error)), platform=self.PLATFORM_ID, url=url) from last_error
        raise ExtractorError("Failed to extract TikTok media", platform=self.PLATFORM_ID, url=url)

    def _build_yt_dlp_attempt_options(self) -> list:
        headers = self.get_headers()
        timeout_seconds = self._read_positive_int_env("YTDLP_TIKTOK_SOCKET_TIMEOUT", 20)
        retry_count = self._read_non_negative_int_env("YTDLP_TIKTOK_RETRIES", 1)

        base_options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "noplaylist": True,
            "format": "bestvideo+bestaudio/best",
            "socket_timeout": timeout_seconds,
            "retries": retry_count,
            "fragment_retries": retry_count,
            "extractor_retries": retry_count,
            "skip_unavailable_fragments": True,
            "http_headers": {
                "User-Agent": headers.get("User-Agent"),
                "Accept-Language": headers.get("Accept-Language", "en-US,en;q=0.9"),
                "Referer": "https://www.tiktok.com/",
            },
        }

        proxy_url = self._resolve_proxy_url()
        if proxy_url:
            base_options["proxy"] = proxy_url

        mobile_agents = [
            ua
            for ua in self.USER_AGENT_POOL
            if "Android" in ua or "iPhone" in ua or "Mobile" in ua
        ]
        if not mobile_agents:
            mobile_agents = [headers.get("User-Agent")]
        mobile_agents = mobile_agents[:3]

        extractor_variants = [
            {},
            {"extractor_args": {"tiktok": {"app_info": ["musical_ly/35.1.3/2023501030/0"]}}},
            {"extractor_args": {"tiktok": {"app_info": ["trill/40.2.4/2024002040/0"]}}},
        ]

        attempts = []
        seen = set()
        for user_agent in mobile_agents:
            for variant in extractor_variants:
                options = copy.deepcopy(base_options)
                options["http_headers"]["User-Agent"] = user_agent
                if variant:
                    options.update(copy.deepcopy(variant))

                key = (
                    options["http_headers"].get("User-Agent"),
                    str(options.get("extractor_args") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                attempts.append(options)

        return attempts

    def _extract_with_public_api(self, url: str) -> dict:
        headers = {
            "User-Agent": self.get_headers().get("User-Agent"),
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.tikwm.com/",
            "Origin": "https://www.tikwm.com",
        }

        request_attempts = (
            ("GET", {"params": {"url": url, "hd": "1"}}),
            ("POST", {"data": {"url": url, "hd": "1"}}),
        )

        last_error = None
        for endpoint in self.PUBLIC_API_ENDPOINTS:
            for method, kwargs in request_attempts:
                try:
                    with self.create_http_client(timeout=25, follow_redirects=True, headers=headers) as client:
                        if method == "GET":
                            response = client.get(endpoint, **kwargs)
                        else:
                            response = client.post(endpoint, **kwargs)
                        response.raise_for_status()
                        payload = response.json()
                except Exception as exc:
                    last_error = exc
                    continue

                try:
                    media_info = self._build_from_public_payload(payload, url)
                except ExtractorError as exc:
                    last_error = exc
                    if self._is_terminal_error(str(exc)):
                        raise
                    continue
                if media_info:
                    return media_info

        if last_error is not None:
            raise ExtractorError("Failed to extract TikTok media", platform=self.PLATFORM_ID, url=url) from last_error
        raise ExtractorError("Failed to extract TikTok media", platform=self.PLATFORM_ID, url=url)

    def _build_from_public_payload(self, payload: dict, url: str) -> dict:
        if not isinstance(payload, dict):
            return None

        code = str(payload.get("code") if payload.get("code") is not None else "").strip().lower()
        data = payload.get("data")
        if isinstance(data, list):
            data = data[0] if data else None

        if code and code not in {"0", "200", "success"} and not isinstance(data, dict):
            message = payload.get("msg") or payload.get("message") or "Failed to extract TikTok media"
            raise ExtractorError(self._friendly_download_error(str(message)), platform=self.PLATFORM_ID, url=url)

        if not isinstance(data, dict):
            return None

        metadata = data.get("author")
        if not isinstance(metadata, dict):
            metadata = {}

        video_data = data.get("video")
        if not isinstance(video_data, dict):
            video_data = {}

        music_info = data.get("music_info")
        if not isinstance(music_info, dict):
            music_info = {}

        video_urls = []
        self._collect_urls(data.get("hdplay"), video_urls)
        self._collect_urls(data.get("play"), video_urls)
        self._collect_urls(data.get("nwm_video_url_hd"), video_urls)
        self._collect_urls(data.get("nwm_video_url"), video_urls)
        self._collect_urls(video_data.get("play_addr"), video_urls)
        self._collect_urls(video_data.get("download_addr"), video_urls)
        self._collect_urls(video_data.get("play"), video_urls)

        wm_urls = []
        self._collect_urls(data.get("wmplay"), wm_urls)

        audio_urls = []
        self._collect_urls(data.get("music"), audio_urls)
        self._collect_urls(data.get("music_url"), audio_urls)
        self._collect_urls(music_info.get("play"), audio_urls)

        video_urls = self._unique_urls(video_urls)
        wm_urls = self._unique_urls(wm_urls)
        audio_urls = [item for item in self._unique_urls(audio_urls) if item not in video_urls]

        clean_video_url = next(
            (item for item in video_urls if "watermark" not in item.lower() and "playwm" not in item.lower()),
            None,
        )
        fallback_video_url = video_urls[0] if video_urls else None
        selected_video_url = clean_video_url or fallback_video_url
        watermark_video_url = next((item for item in wm_urls if item != selected_video_url), None)
        if not watermark_video_url:
            watermark_video_url = next(
                (item for item in video_urls if item != selected_video_url and ("watermark" in item.lower() or "playwm" in item.lower())),
                None,
            )

        qualities = []
        if selected_video_url:
            qualities.append(
                {
                    "label": "best",
                    "display_label": "best",
                    "selector": "tiktok_public_best",
                    "format_id": None,
                    "url": selected_video_url,
                    "size_bytes": self._to_int(data.get("hd_size") or data.get("size")),
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": self._infer_format(selected_video_url),
                    "has_audio": True,
                    "is_hls": ".m3u8" in selected_video_url,
                }
            )

        if watermark_video_url:
            qualities.append(
                {
                    "label": "watermarked",
                    "display_label": "watermarked",
                    "selector": "tiktok_public_wm",
                    "format_id": None,
                    "url": watermark_video_url,
                    "size_bytes": self._to_int(data.get("wm_size") or data.get("size")),
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": self._infer_format(watermark_video_url),
                    "has_audio": True,
                    "is_hls": ".m3u8" in watermark_video_url,
                }
            )

        if audio_urls:
            qualities.append(
                {
                    "label": "audio_only",
                    "display_label": "audio_only",
                    "selector": "tiktok_public_audio",
                    "format_id": None,
                    "url": audio_urls[0],
                    "size_bytes": self._to_int(data.get("music_size")),
                    "codec": "aac",
                    "bitrate": None,
                    "hdr": False,
                    "format": self._infer_format(audio_urls[0], default_ext="m4a"),
                    "has_audio": True,
                    "is_hls": ".m3u8" in audio_urls[0],
                }
            )

        if not qualities:
            return None

        create_time = data.get("create_time") or data.get("createTime")
        upload_date = self._normalize_upload_date(create_time)

        title = data.get("title") or data.get("desc") or "TikTok Video"
        author_name = metadata.get("nickname") or data.get("author")
        channel_id = metadata.get("unique_id") or metadata.get("id")
        thumbnail = data.get("cover") or data.get("origin_cover") or data.get("ai_dynamic_cover")
        duration = self._to_int(data.get("duration"))
        view_count = self._to_int(data.get("play_count") or data.get("view_count"))
        description = (data.get("title") or data.get("desc") or "")[:500] or None

        manifest_url = None
        for quality in qualities:
            if quality.get("is_hls"):
                manifest_url = quality.get("url")
                break

        return {
            "title": title,
            "author": author_name,
            "channel_id": channel_id,
            "thumbnail": thumbnail,
            "duration": duration,
            "view_count": view_count,
            "description": description,
            "upload_date": upload_date,
            "qualities": self._sort_qualities(qualities),
            "subtitles": [],
            "chapters": [],
            "is_hls": bool(manifest_url and len(qualities) == 1),
            "manifest_url": manifest_url,
            "headers_required": {"Referer": "https://www.tiktok.com/"},
            "platform": self.PLATFORM_ID,
        }

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
                best_audio_format = fmt
                best_audio_bitrate = bitrate

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

            is_audio_only = self._is_audio_only_format(fmt)
            label = "audio_only" if is_audio_only else self._label_for_height(fmt.get("height"))
            if not label and not is_audio_only:
                continue

            format_id = str(fmt.get("format_id") or "").strip()
            selector = format_id or f"tiktok_{index}_{fmt.get('ext') or 'bin'}"
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

            entry = {
                "label": label,
                "display_label": label,
                "selector": selector,
                "format_id": format_id or None,
                "url": media_url,
                "size_bytes": size_bytes,
                "codec": fmt.get("acodec") if is_audio_only else (fmt.get("vcodec") if fmt.get("vcodec") != "none" else fmt.get("acodec")),
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
                    "format": info.get("ext") or "mp4",
                    "is_hls": ".m3u8" in str(single_url),
                    "has_audio": True,
                }
            )

        if not qualities:
            raise ExtractorError("No downloadable TikTok formats available", platform=self.PLATFORM_ID, url=url)

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
        headers_required.setdefault("Referer", "https://www.tiktok.com/")

        return {
            "title": info.get("title") or "TikTok Video",
            "author": info.get("uploader") or info.get("channel") or info.get("creator"),
            "channel_id": info.get("channel_id") or info.get("uploader_id"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "view_count": info.get("view_count") or info.get("play_count"),
            "description": (info.get("description") or "")[:500] if info.get("description") else None,
            "upload_date": self._normalize_upload_date(info.get("upload_date")),
            "qualities": qualities,
            "subtitles": subtitles,
            "chapters": chapters,
            "is_hls": bool(manifest_url and not any(not item.get("is_hls") for item in qualities)),
            "manifest_url": manifest_url,
            "headers_required": headers_required,
        }

    def _ensure_windows_proactor_policy(self):
        if os.name != "nt":
            return

        policy_cls = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
        if policy_cls is None:
            return

        current_policy = asyncio.get_event_loop_policy()
        if isinstance(current_policy, policy_cls):
            return

        asyncio.set_event_loop_policy(policy_cls())

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

    def _read_positive_int_env(self, env_name: str, default_value: int) -> int:
        raw = str(os.environ.get(env_name, "")).strip()
        if not raw:
            return default_value
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            return default_value
        return default_value

    def _read_non_negative_int_env(self, env_name: str, default_value: int) -> int:
        raw = str(os.environ.get(env_name, "")).strip()
        if not raw:
            return default_value
        try:
            value = int(raw)
            if value >= 0:
                return value
        except ValueError:
            return default_value
        return default_value

    def _is_valid_media_info(self, media_info: dict) -> bool:
        if not isinstance(media_info, dict):
            return False
        qualities = media_info.get("qualities")
        if "title" not in media_info:
            return False
        if not isinstance(qualities, list):
            return False
        if not qualities:
            return False
        return True

    def _is_terminal_error(self, message: str) -> bool:
        lowered = (message or "").lower()
        if not lowered:
            return False
        return any(marker in lowered for marker in self._TERMINAL_ERROR_MARKERS)

    def _normalize_url(self, value):
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.startswith("//"):
            candidate = f"https:{candidate}"
        if not candidate.lower().startswith(("http://", "https://")):
            return None
        return candidate

    def _collect_urls(self, value, output: list, depth: int = 0):
        if value is None or depth > 4:
            return
        if isinstance(value, str):
            normalized = self._normalize_url(value)
            if normalized:
                output.append(normalized)
            return
        if isinstance(value, dict):
            for nested in value.values():
                self._collect_urls(nested, output, depth + 1)
            return
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                self._collect_urls(nested, output, depth + 1)

    def _unique_urls(self, values: list) -> list:
        result = []
        seen = set()
        for item in values:
            if not item or item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    def _infer_format(self, media_url: str, default_ext: str = "mp4") -> str:
        value = str(media_url or "").strip()
        if not value:
            return default_ext
        parsed = urlparse(value)
        path = parsed.path or ""
        if "." not in path:
            return default_ext
        extension = path.rsplit(".", 1)[-1].lower()
        if not extension:
            return default_ext
        return extension

    def _to_int(self, value):
        if value is None:
            return None
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None

    def _normalize_upload_date(self, value):
        raw = str(value or "").strip()
        if not raw:
            return None
        if re.fullmatch(r"\d{10,13}", raw):
            try:
                timestamp = int(raw)
                if len(raw) == 13:
                    timestamp = int(timestamp / 1000)
                return datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OSError):
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

    def _friendly_download_error(self, message: str) -> str:
        lowered = (message or "").lower()
        if any(marker in lowered for marker in self._TERMINAL_ERROR_MARKERS):
            return "This TikTok video is private or unavailable."
        if any(marker in lowered for marker in self._RETRYABLE_ERROR_MARKERS):
            return "TikTok is temporarily unreachable. Please retry after a short time."
        return message or "Failed to extract TikTok media"

    def _extract_aweme(self, data):
        if not isinstance(data, dict):
            return None
        if "aweme_detail" in data:
            return data.get("aweme_detail")
        if "item_list" in data and data.get("item_list"):
            return data["item_list"][0]
        item_info = data.get("itemInfo") or {}
        if "itemStruct" in item_info:
            return item_info.get("itemStruct")
        return None

    def _pick_video_url(self, video):
        play = video.get("play_addr") or {}
        download = video.get("download_addr") or {}
        urls = play.get("url_list") or []
        urls.extend(download.get("url_list") or [])
        for url in urls:
            if "watermark" not in url and "playwm" not in url:
                return url
        return urls[0] if urls else None

    def _pick_audio_url(self, aweme):
        music = aweme.get("music") or {}
        play_url = music.get("play_url") or {}
        url_list = play_url.get("url_list") or []
        return url_list[0] if url_list else None
