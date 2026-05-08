import logging
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import yt_dlp

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class BilibiliExtractor(BaseExtractor):
    PLATFORM_ID = "bilibili"
    REQUIRES_HEADLESS = False
    REQUIRES_PROXY = False
    TEST_URL = "https://www.bilibili.com/video/BV1GJ411x7h7"

    _TERMINAL_ERROR_MARKERS = (
        "private",
        "not available",
        "forbidden",
        "会员",
        "member only",
        "requires login",
        "premium",
        "paid",
        "removed",
    )

    def extract(self, url: str) -> dict:
        normalized_url = self._normalize_url(url)

        api_error = None
        try:
            return self._extract_with_open_api(normalized_url)
        except Exception as exc:
            api_error = exc
            logger.warning("Bilibili API extraction failed for %s: %s", normalized_url, exc)

        try:
            return self._extract_with_yt_dlp(normalized_url)
        except Exception as exc:
            if isinstance(exc, ExtractorError) and self._is_terminal_error(str(exc)):
                raise
            if isinstance(api_error, ExtractorError) and self._is_terminal_error(str(api_error)):
                raise api_error
            if isinstance(exc, ExtractorError):
                raise
            if isinstance(api_error, ExtractorError):
                raise api_error
            raise ExtractorError("Failed to fetch Bilibili metadata", platform=self.PLATFORM_ID, url=normalized_url) from exc

    def _normalize_url(self, url: str) -> str:
        value = str(url or "").strip()
        if not value:
            return value

        parsed = urlparse(value)
        if not parsed.scheme:
            value = f"https://{value.lstrip('/')}"
            parsed = urlparse(value)

        raw = value
        bvid_match = re.search(r"BV[0-9A-Za-z]{10}", raw, re.IGNORECASE)
        if bvid_match:
            bvid = bvid_match.group(0)
            if not bvid.startswith("BV"):
                bvid = f"BV{bvid[2:]}"
            return f"https://www.bilibili.com/video/{bvid}"

        avid_match = re.search(r"av(\d+)", raw, re.IGNORECASE)
        if avid_match:
            return f"https://www.bilibili.com/video/av{avid_match.group(1)}"

        host = (parsed.hostname or "").lower()
        if host in {"m.bilibili.com", "bilibili.com", "www.bilibili.com"}:
            query_items = parse_qsl(parsed.query, keep_blank_values=True)
            query = urlencode(query_items, doseq=True)
            return urlunparse(
                (
                    parsed.scheme or "https",
                    "www.bilibili.com",
                    parsed.path or "/",
                    "",
                    query,
                    "",
                )
            )

        return value

    def _extract_with_open_api(self, url: str) -> dict:
        bvid, avid = self._extract_ids(url)
        info_url = "https://api.bilibili.com/x/web-interface/view"
        params = {"bvid": bvid} if bvid else {"aid": avid}
        headers = {
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
        }
        try:
            response = self.http_get(info_url, params=params, headers=headers)
            payload = response.json()
        except ExtractorError:
            raise
        except Exception as exc:
            raise ExtractorError("Failed to fetch Bilibili metadata", platform=self.PLATFORM_ID, url=url) from exc

        payload_code = payload.get("code")
        if payload_code == -412:
            raise ExtractorError("Bilibili rate limit reached", platform=self.PLATFORM_ID, url=url)
        if payload_code not in (None, 0):
            payload_message = payload.get("message") or payload.get("msg") or "Bilibili metadata API failed"
            raise ExtractorError(f"{payload_message} (code: {payload_code})", platform=self.PLATFORM_ID, url=url)

        data = payload.get("data") or {}
        if not data:
            raise ExtractorError("Bilibili response missing data", platform=self.PLATFORM_ID, url=url)

        pages = data.get("pages") or []
        cid = pages[0].get("cid") if pages else None
        if not cid:
            raise ExtractorError("Bilibili content id missing", platform=self.PLATFORM_ID, url=url)

        play_url = "https://api.bilibili.com/x/player/playurl"
        play_params = {
            "avid": data.get("aid") or avid,
            "cid": cid,
            "qn": 120,
            "fnval": 4048,
            "fourk": 1,
        }
        try:
            play_response = self.http_get(play_url, params=play_params, headers=headers)
            play_payload = play_response.json()
            play_code = play_payload.get("code")
            if play_code not in (None, 0):
                play_message = play_payload.get("message") or play_payload.get("msg") or "Bilibili stream API failed"
                raise ExtractorError(f"{play_message} (code: {play_code})", platform=self.PLATFORM_ID, url=url)
            play_data = play_payload.get("data") or {}
        except ExtractorError:
            raise
        except Exception as exc:
            raise ExtractorError("Failed to fetch Bilibili streams", platform=self.PLATFORM_ID, url=url) from exc

        qualities = []
        audio_url = None
        dash = play_data.get("dash") or {}
        videos = dash.get("video") or []
        audios = dash.get("audio") or []
        if audios:
            best_audio = max(audios, key=lambda item: item.get("bandwidth") or 0)
            audio_url = self._pick_media_url(best_audio)

        for index, video in enumerate(videos):
            video_url = self._pick_media_url(video)
            if not video_url:
                continue
            label = self._label_for_quality(video.get("id"), video.get("height"))
            format_value = str(video.get("mimeType") or "video/mp4").split("/")[-1]
            qualities.append(
                {
                    "label": label,
                    "display_label": label,
                    "selector": str(video.get("id") or f"bilibili_{index}"),
                    "format_id": str(video.get("id")) if video.get("id") is not None else None,
                    "url": video_url,
                    "size_bytes": None,
                    "codec": video.get("codecs"),
                    "bitrate": video.get("bandwidth"),
                    "hdr": False,
                    "has_audio": False,
                    "format": format_value,
                    "audio_url": audio_url,
                }
            )

        if not qualities:
            durl_list = play_data.get("durl") or []
            for index, item in enumerate(durl_list):
                stream_url = self._pick_media_url(item)
                if not stream_url:
                    continue
                qualities.append(
                    {
                        "label": "original",
                        "display_label": "original",
                        "selector": f"bilibili_durl_{index}",
                        "format_id": None,
                        "url": stream_url,
                        "size_bytes": item.get("size"),
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "format": "flv",
                        "has_audio": True,
                    }
                )

        if not qualities:
            raise ExtractorError("No downloadable Bilibili streams available", platform=self.PLATFORM_ID, url=url)

        qualities = self._sort_qualities(qualities)

        upload_date = None
        if data.get("pubdate"):
            upload_date = datetime.utcfromtimestamp(data.get("pubdate")).strftime("%Y-%m-%d")

        return {
            "title": data.get("title"),
            "author": (data.get("owner") or {}).get("name"),
            "channel_id": None,
            "thumbnail": data.get("pic"),
            "duration": data.get("duration"),
            "view_count": (data.get("stat") or {}).get("view"),
            "description": data.get("desc"),
            "upload_date": upload_date,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {"Referer": headers.get("Referer"), "Origin": headers.get("Origin")},
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
                "Referer": "https://www.bilibili.com/",
            },
        }

        proxy_url = self._resolve_proxy_url()
        if proxy_url:
            ydl_opts["proxy"] = proxy_url

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise ExtractorError(self._friendly_download_error(str(exc)), platform=self.PLATFORM_ID, url=url) from exc

        if isinstance(info, dict) and info.get("entries"):
            first_entry = next((entry for entry in info.get("entries") or [] if entry), None)
            if first_entry:
                info = first_entry

        return self._build_from_yt_dlp_info(info, url)

    def _build_from_yt_dlp_info(self, info: dict, url: str) -> dict:
        if not isinstance(info, dict):
            raise ExtractorError("Invalid Bilibili metadata from yt-dlp", platform=self.PLATFORM_ID, url=url)

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

            is_audio_only = self._is_audio_only_format(fmt)
            if is_audio_only:
                label = "audio_only"
            else:
                label = self._label_for_quality(None, fmt.get("height"))
            if not label and not is_audio_only:
                continue

            format_id = str(fmt.get("format_id") or "").strip()
            selector = format_id or f"bilibili_{index}_{fmt.get('ext') or 'bin'}"
            if selector in seen_selectors:
                continue
            seen_selectors.add(selector)

            bitrate = fmt.get("tbr") or fmt.get("abr")
            bitrate_bps = self._to_bps(bitrate)
            size_bytes = fmt.get("filesize") or fmt.get("filesize_approx")
            acodec = str(fmt.get("acodec") or "").strip().lower()
            vcodec = str(fmt.get("vcodec") or "").strip().lower()
            has_audio = True if is_audio_only else bool(acodec and acodec != "none")
            if not has_audio and best_audio_size and size_bytes:
                size_bytes = size_bytes + best_audio_size

            entry = {
                "label": label,
                "display_label": label,
                "selector": selector,
                "format_id": format_id or None,
                "url": media_url,
                "size_bytes": size_bytes,
                "codec": fmt.get("acodec") if is_audio_only else (fmt.get("vcodec") if vcodec != "none" else fmt.get("acodec")),
                "bitrate": bitrate_bps,
                "hdr": "hdr" in str(fmt.get("dynamic_range") or "").lower(),
                "format": fmt.get("ext") or "mp4",
                "has_audio": has_audio,
            }

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
                    "has_audio": True,
                }
            )

        if not qualities:
            raise ExtractorError("No downloadable Bilibili streams available", platform=self.PLATFORM_ID, url=url)

        qualities = self._sort_qualities(qualities)

        upload_date = self._normalize_upload_date(info.get("upload_date") or info.get("timestamp"))

        return {
            "title": info.get("title"),
            "author": info.get("uploader") or info.get("channel"),
            "channel_id": info.get("channel_id"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "description": (info.get("description") or "")[:500] if info.get("description") else None,
            "upload_date": upload_date,
            "qualities": qualities,
            "subtitles": self._parse_subtitles(info.get("subtitles") or {}),
            "chapters": self._parse_chapters(info.get("chapters") or []),
            "is_hls": any(".m3u8" in str(item.get("url") or "") for item in qualities),
            "manifest_url": self._first_manifest_url(qualities),
            "headers_required": info.get("http_headers") or {"Referer": "https://www.bilibili.com/"},
        }

    def _friendly_download_error(self, message: str) -> str:
        lowered = (message or "").lower()
        if "private" in lowered or "forbidden" in lowered:
            return "This Bilibili content is private or restricted"
        if "429" in lowered or "too many requests" in lowered:
            return "Bilibili rate limit reached"
        return message or "Failed to extract Bilibili media"

    def _is_terminal_error(self, message: str) -> bool:
        value = (message or "").lower()
        if not value:
            return False
        return any(marker in value for marker in self._TERMINAL_ERROR_MARKERS)

    def _pick_media_url(self, payload: dict):
        if not isinstance(payload, dict):
            return None
        if payload.get("baseUrl"):
            return payload.get("baseUrl")
        if payload.get("base_url"):
            return payload.get("base_url")
        backup = payload.get("backupUrl") or payload.get("backup_url") or []
        if isinstance(backup, list) and backup:
            return backup[0]
        if payload.get("url"):
            return payload.get("url")
        return None

    def _is_audio_only_format(self, fmt: dict) -> bool:
        vcodec = str(fmt.get("vcodec") or "").strip().lower()
        acodec = str(fmt.get("acodec") or "").strip().lower()
        return vcodec == "none" and acodec not in {"", "none"}

    def _to_bps(self, value):
        if value is None:
            return None
        try:
            return int(float(value) * 1000)
        except (TypeError, ValueError):
            return None

    def _sort_qualities(self, qualities: list) -> list:
        return sorted(
            qualities,
            key=lambda item: (
                1 if self._is_audio_only_quality(item) else 2,
                self._quality_rank(item.get("label")),
                item.get("bitrate") or 0,
            ),
            reverse=True,
        )

    def _is_audio_only_quality(self, item: dict) -> bool:
        label = str((item or {}).get("label") or "").strip().lower()
        return label in {"audio", "audio_only", "audio only"}

    def _quality_rank(self, label: str) -> int:
        value = str(label or "").strip().lower()
        mapping = {
            "8k": 4320,
            "4k": 2160,
            "2k": 1440,
            "1440p": 1440,
            "1080p60": 1081,
            "1080p+": 1081,
            "1080p": 1080,
            "720p": 720,
            "480p": 480,
            "360p": 360,
            "original": 999,
            "best": 1000,
            "audio_only": 0,
        }
        if value in mapping:
            return mapping[value]
        match = re.search(r"(\d{3,4})p", value)
        if match:
            return int(match.group(1))
        return -1

    def _normalize_upload_date(self, value):
        if not value:
            return None
        if isinstance(value, (int, float)):
            try:
                return datetime.utcfromtimestamp(int(value)).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                return None
        text = str(value).strip()
        if not text:
            return None
        if re.fullmatch(r"\d{8}", text):
            try:
                return datetime.strptime(text, "%Y%m%d").strftime("%Y-%m-%d")
            except ValueError:
                return None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
        return None

    def _parse_subtitles(self, subtitles_data: dict) -> list:
        items = []
        for lang, entries in (subtitles_data or {}).items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                subtitle_url = entry.get("url")
                if not subtitle_url:
                    continue
                items.append(
                    {
                        "lang": lang,
                        "label": entry.get("name") or lang,
                        "url": subtitle_url,
                        "format": entry.get("ext") or "vtt",
                    }
                )
                break
        return items

    def _parse_chapters(self, chapters_data: list) -> list:
        chapters = []
        for chapter in chapters_data or []:
            start = chapter.get("start_time") or 0
            chapters.append(
                {
                    "title": chapter.get("title") or "Chapter",
                    "start_ms": int(float(start) * 1000),
                }
            )
        return chapters

    def _first_manifest_url(self, qualities: list):
        for item in qualities or []:
            url = str(item.get("url") or "")
            if ".m3u8" in url:
                return url
        return None

    def _extract_ids(self, url: str):
        bvid_match = re.search(r"BV[0-9A-Za-z]{10}", url, re.IGNORECASE)
        if bvid_match:
            bvid = bvid_match.group(0)
            if not bvid.startswith("BV"):
                bvid = f"BV{bvid[2:]}"
            return bvid, None
        avid_match = re.search(r"av(\d+)", url, re.IGNORECASE)
        if avid_match:
            return None, int(avid_match.group(1))
        query_bvid_match = re.search(r"[?&]bvid=(BV[0-9A-Za-z]{10})", url, re.IGNORECASE)
        if query_bvid_match:
            bvid = query_bvid_match.group(1)
            if not bvid.startswith("BV"):
                bvid = f"BV{bvid[2:]}"
            return bvid, None
        query_aid_match = re.search(r"[?&]aid=(\d+)", url, re.IGNORECASE)
        if query_aid_match:
            return None, int(query_aid_match.group(1))
        raise ExtractorError("Invalid Bilibili URL", platform=self.PLATFORM_ID, url=url)

    def _label_for_quality(self, qn, height):
        mapping = {
            120: "4K",
            116: "1080p60",
            112: "1080p+",
            80: "1080p",
            64: "720p",
            32: "480p",
            16: "360p",
        }
        if qn in mapping:
            return mapping[qn]
        if height:
            return f"{height}p"
        return "original"
