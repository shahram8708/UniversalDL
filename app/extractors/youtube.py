import copy
import logging
import os
import re
from datetime import datetime

import yt_dlp

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class YoutubeExtractor(BaseExtractor):
    PLATFORM_ID = "youtube"
    REQUIRES_HEADLESS = False
    REQUIRES_PROXY = False
    TEST_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    AUTH_CHALLENGE_MARKERS = (
        "sign in to confirm you",
        "cookies-from-browser",
        "cookies for the authentication",
        "use --cookies",
        "age-restricted",
    )

    TERMINAL_ERROR_MARKERS = (
        "video unavailable",
        "private video",
        "this video is private",
        "premieres in",
        "members-only",
        "has been removed",
    )

    def extract(self, url: str) -> dict:
        try:
            info = self.extract_info(url)
        except yt_dlp.utils.DownloadError as exc:
            raise ExtractorError(self._friendly_download_error(str(exc)), platform=self.PLATFORM_ID, url=url) from exc

        title = info.get("title")
        upload_date = info.get("upload_date")
        formatted_upload_date = None
        if upload_date:
            try:
                formatted_upload_date = datetime.strptime(upload_date, "%Y%m%d").strftime("%Y-%m-%d")
            except ValueError:
                formatted_upload_date = None

        formats = info.get("formats", [])

        best_audio_format = None
        best_audio_bitrate = 0
        for fmt in formats:
            if not fmt.get("url"):
                continue
            vcodec = fmt.get("vcodec")
            acodec = fmt.get("acodec")
            is_audio_only = vcodec == "none" and acodec and acodec != "none"
            if not is_audio_only:
                continue
            bitrate = fmt.get("abr") or fmt.get("tbr") or 0
            if bitrate > best_audio_bitrate:
                best_audio_bitrate = bitrate
                best_audio_format = fmt

        best_audio_url = best_audio_format.get("url") if best_audio_format else None
        best_audio_size = None
        if best_audio_format:
            best_audio_size = best_audio_format.get("filesize") or best_audio_format.get("filesize_approx")

        qualities = []
        seen_selectors = set()

        for index, fmt in enumerate(formats):
            if not fmt.get("url"):
                continue

            vcodec = fmt.get("vcodec")
            acodec = fmt.get("acodec")
            height = fmt.get("height")
            fps = fmt.get("fps")
            is_audio_only = vcodec == "none" and acodec and acodec != "none"
            has_audio = bool(acodec and acodec != "none")
            label = self._label_for_height(height) if not is_audio_only else "audio_only"

            if not label:
                continue
            if not is_audio_only and not height:
                continue

            bitrate = fmt.get("tbr") or fmt.get("abr")
            bitrate_bps = None
            if bitrate:
                try:
                    bitrate_bps = int(float(bitrate) * 1000)
                except (TypeError, ValueError):
                    bitrate_bps = None

            fps_value = None
            if fps:
                try:
                    fps_value = int(round(float(fps)))
                except (TypeError, ValueError):
                    fps_value = None

            hdr_note = str(fmt.get("dynamic_range") or fmt.get("format_note") or "").lower()
            hdr = "hdr" in hdr_note or "dolby" in hdr_note
            size_bytes = fmt.get("filesize") or fmt.get("filesize_approx")
            if not has_audio and best_audio_size and size_bytes:
                size_bytes = size_bytes + best_audio_size

            format_id = str(fmt.get("format_id") or "").strip()
            selector = format_id or f"{label}_{fmt.get('ext') or 'bin'}_{fps_value or 0}_{bitrate_bps or 0}_{index}"
            if selector in seen_selectors:
                continue
            seen_selectors.add(selector)

            entry = {
                "label": label,
                "display_label": "",
                "selector": selector,
                "format_id": format_id or None,
                "url": fmt.get("url"),
                "size_bytes": size_bytes,
                "codec": acodec if is_audio_only else (vcodec if vcodec != "none" else acodec),
                "bitrate": bitrate_bps,
                "hdr": hdr,
                "fps": fps_value,
                "format": fmt.get("ext") or "mp4",
                "has_audio": has_audio,
            }
            if not is_audio_only and not has_audio and best_audio_url:
                entry["audio_url"] = best_audio_url

            entry["display_label"] = self._build_display_label(entry, is_audio_only)
            qualities.append(entry)

        qualities = self._sort_qualities(qualities)

        subtitles = []
        subtitles_data = info.get("subtitles") or {}
        auto_captions = info.get("automatic_captions") or {}
        subtitles.extend(self._parse_subtitles(subtitles_data))
        subtitles.extend(self._parse_subtitles(auto_captions))

        chapters = []
        for chapter in info.get("chapters") or []:
            start = chapter.get("start_time") or 0
            chapters.append({"title": chapter.get("title") or "Chapter", "start_ms": int(start * 1000)})

        is_hls = False
        manifest_url = None
        for fmt in formats:
            url_value = fmt.get("url") or ""
            if ".m3u8" in url_value:
                is_hls = True
                manifest_url = url_value
                break

        return {
            "title": title,
            "author": info.get("uploader"),
            "channel_id": info.get("channel_id"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "description": (info.get("description") or "")[:500] if info.get("description") else None,
            "upload_date": formatted_upload_date,
            "qualities": qualities,
            "subtitles": subtitles,
            "chapters": chapters,
            "is_hls": is_hls,
            "manifest_url": manifest_url,
            "headers_required": info.get("http_headers") or {},
        }

    def extract_info(self, url: str, option_overrides: dict = None) -> dict:
        return self._extract_info_with_fallback(url, option_overrides=option_overrides)

    def _extract_info_with_fallback(self, url: str, option_overrides: dict = None) -> dict:
        base_options = self._build_base_ydl_options(option_overrides=option_overrides)
        last_error = None
        auth_challenge_seen = False

        for ydl_opts in self._build_attempt_options(base_options):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)
            except yt_dlp.utils.DownloadError as exc:
                last_error = exc
                message = str(exc).lower()
                if self._is_terminal_error(message):
                    break
                if self._is_auth_challenge(message):
                    auth_challenge_seen = True
                continue

        if auth_challenge_seen:
            for cookie_option in self._build_cookie_fallbacks():
                for ydl_opts in self._build_attempt_options(base_options, cookie_option):
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            return ydl.extract_info(url, download=False)
                    except yt_dlp.utils.DownloadError as exc:
                        last_error = exc
                        if self._is_terminal_error(str(exc).lower()):
                            break
                        continue

        if last_error:
            raise last_error
        raise yt_dlp.utils.DownloadError("YouTube extraction failed")

    def _build_base_ydl_options(self, option_overrides: dict = None) -> dict:
        headers = self.get_headers()
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "writesubtitles": False,
            "skip_download": True,
            "format": "bestvideo+bestaudio/best",
            "socket_timeout": 30,
            "noplaylist": True,
            "retries": 2,
            "fragment_retries": 2,
            "extractor_retries": 2,
            "skip_unavailable_fragments": True,
            "http_headers": {
                "User-Agent": headers.get("User-Agent"),
                "Accept-Language": headers.get("Accept-Language", "en-US,en;q=0.9"),
            },
        }

        proxy_url = self._get_proxy_url()
        if proxy_url:
            ydl_opts["proxy"] = proxy_url

        env_cookie_file = (os.environ.get("YTDLP_COOKIES_FILE") or "").strip()
        env_cookie_browser = (os.environ.get("YTDLP_COOKIES_FROM_BROWSER") or "").strip()

        if env_cookie_file and os.path.exists(env_cookie_file):
            ydl_opts["cookiefile"] = env_cookie_file
        else:
            browser_tuple = self._parse_browser_cookie_value(env_cookie_browser)
            if browser_tuple:
                ydl_opts["cookiesfrombrowser"] = browser_tuple

        if option_overrides:
            ydl_opts.update(option_overrides)

        return ydl_opts

    def _build_attempt_options(self, base_options: dict, extra_options: dict = None) -> list:
        attempts = []
        for clients in self._player_client_attempts():
            attempt = copy.deepcopy(base_options)
            attempt["extractor_args"] = {"youtube": {"player_client": clients}}

            if extra_options:
                if "cookiesfrombrowser" in extra_options:
                    attempt.pop("cookiefile", None)
                if "cookiefile" in extra_options:
                    attempt.pop("cookiesfrombrowser", None)
                attempt.update(extra_options)

            attempts.append(attempt)
        return attempts

    def _player_client_attempts(self) -> list:
        configured = (os.environ.get("YTDLP_YOUTUBE_CLIENTS") or "android,web,ios,tv_embedded").strip()
        configured_clients = [item.strip() for item in configured.split(",") if item.strip()]
        sequences = [
            configured_clients,
            ["android", "web"],
            ["tv_embedded", "android", "ios"],
            ["ios", "android", "web"],
        ]

        deduped = []
        seen = set()
        for sequence in sequences:
            values = tuple(sequence)
            if not values or values in seen:
                continue
            seen.add(values)
            deduped.append(list(values))
        return deduped

    def _build_cookie_fallbacks(self) -> list:
        options = []
        seen = set()

        env_cookie_file = (os.environ.get("YTDLP_COOKIES_FILE") or "").strip()
        if env_cookie_file and os.path.exists(env_cookie_file):
            key = ("cookiefile", env_cookie_file)
            seen.add(key)
            options.append({"cookiefile": env_cookie_file})

        env_browser = self._parse_browser_cookie_value(os.environ.get("YTDLP_COOKIES_FROM_BROWSER"))
        if env_browser:
            key = ("cookiesfrombrowser", env_browser)
            if key not in seen:
                seen.add(key)
                options.append({"cookiesfrombrowser": env_browser})

        for browser in ("chrome", "edge", "firefox", "brave"):
            key = ("cookiesfrombrowser", (browser,))
            if key in seen:
                continue
            seen.add(key)
            options.append({"cookiesfrombrowser": (browser,)})

        return options

    def _parse_browser_cookie_value(self, value):
        if not value:
            return None
        parts = [item.strip() for item in str(value).split(":") if item.strip()]
        if not parts:
            return None
        return tuple(parts[:4])

    def _get_proxy_url(self):
        if isinstance(self.proxy, dict):
            return self.proxy.get("https://") or self.proxy.get("http://")
        if isinstance(self.proxy, str):
            return self.proxy
        return None

    def _is_auth_challenge(self, message: str) -> bool:
        if not message:
            return False
        return any(marker in message for marker in self.AUTH_CHALLENGE_MARKERS)

    def _is_terminal_error(self, message: str) -> bool:
        if not message:
            return False
        return any(marker in message for marker in self.TERMINAL_ERROR_MARKERS)

    def _friendly_download_error(self, message: str) -> str:
        lowered = (message or "").lower()
        if self._is_auth_challenge(lowered):
            return (
                "YouTube requested bot verification. Set YTDLP_COOKIES_FROM_BROWSER or "
                "YTDLP_COOKIES_FILE in environment, then retry."
            )
        return message

    def _label_for_height(self, height):
        if not height:
            return None
        mapping = {
            4320: "8K",
            2160: "4K",
            1440: "2K",
            1080: "1080p",
            720: "720p",
            480: "480p",
            360: "360p",
            240: "240p",
            144: "144p",
        }
        return mapping.get(height) or f"{height}p"

    def _sort_qualities(self, qualities):
        return sorted(qualities, key=self._quality_sort_key)

    def _quality_sort_key(self, item):
        label = str(item.get("label") or "").strip().lower()
        bitrate = item.get("bitrate") or 0
        fps = item.get("fps") or 0

        if label in {"audio_only", "audio", "audio only"}:
            return (1, 0, 0, -bitrate)

        height = self._height_from_label(label)
        return (0, -height, -fps, -bitrate)

    def _height_from_label(self, label: str) -> int:
        value = str(label or "").strip().lower()
        if value == "8k":
            return 4320
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

    def _build_display_label(self, quality: dict, is_audio_only: bool) -> str:
        parts = []
        label = quality.get("label")
        bitrate = quality.get("bitrate")
        fps = quality.get("fps")
        codec = self._normalize_codec_name(quality.get("codec"))
        output_format = str(quality.get("format") or "").upper()

        if is_audio_only:
            parts.append("Audio")
        else:
            parts.append(label if label and label != "audio_only" else "Video")
            if fps and fps >= 50:
                parts.append(f"{fps}fps")
            if quality.get("hdr"):
                parts.append("HDR")
            if not quality.get("has_audio"):
                parts.append("Video Only")

        if bitrate:
            kbps = max(1, int(round(bitrate / 1000)))
            parts.append(f"{kbps}kbps")

        if codec:
            parts.append(codec.upper())
        if output_format:
            parts.append(output_format)

        return " ".join(parts).strip()

    def _normalize_codec_name(self, codec_value) -> str:
        value = str(codec_value or "").strip().lower()
        if not value or value == "none":
            return ""
        value = value.split(".", 1)[0]
        if value == "mp4a":
            return "aac"
        return value

    def _parse_subtitles(self, subtitles_data):
        items = []
        for lang, entries in subtitles_data.items():
            entry = self._pick_preferred_subtitle_entry(entries)
            if not entry:
                continue
            items.append(
                {
                    "lang": lang,
                    "label": lang,
                    "url": entry.get("url"),
                    "format": entry.get("ext") or "vtt",
                }
            )
        return items

    def _pick_preferred_subtitle_entry(self, entries):
        if not entries:
            return None

        preferred_ext_order = {
            "vtt": 0,
            "srt": 1,
            "ttml": 2,
            "srv3": 3,
            "srv2": 4,
            "srv1": 5,
            "json3": 9,
        }

        best_entry = None
        best_score = 999

        for entry in entries:
            if not entry:
                continue
            url = str(entry.get("url") or "")
            if not url:
                continue

            ext = str(entry.get("ext") or "").lower()
            score = preferred_ext_order.get(ext, 50)
            if "tlang=" in url:
                score += 20

            if score < best_score:
                best_entry = entry
                best_score = score
                if score == 0:
                    break

        if best_entry:
            return best_entry

        for entry in entries:
            if entry and entry.get("url"):
                return entry

        return None
