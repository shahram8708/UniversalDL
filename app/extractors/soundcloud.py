import logging
import os
import re
from datetime import datetime
from typing import List, Tuple
from urllib.parse import urljoin

import httpx
import yt_dlp

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class SoundCloudExtractor(BaseExtractor):
    PLATFORM_ID = "soundcloud"
    REQUIRES_HEADLESS = False
    REQUIRES_PROXY = False
    TEST_URL = "https://soundcloud.com/marshmellomusic/alone"

    _FALLBACK_CLIENT_IDS = [
        "Z9G0vQ8h2lm1t8q5y7bV7l9t2eY3I4J0",
        "3L7HVU2tV9Z4yZ7k4PpO8aTq9F8jG6Hd",
    ]

    _CLIENT_ID_PATTERNS = (
        r'client_id"\s*:\s*"([a-zA-Z0-9_-]{16,64})"',
        r'client_id\\"\s*:\s*\\"([a-zA-Z0-9_-]{16,64})\\"',
        r"client_id=([a-zA-Z0-9_-]{16,64})",
    )

    _STREAM_KEYS = (
        ("http_mp3_128_url", "mp3", False, 128000),
        ("hls_mp3_128_url", "m4a", True, 128000),
        ("hls_url", "m4a", True, None),
        ("preview_mp3_128_url", "mp3", False, 128000),
    )

    def extract(self, url: str) -> dict:
        api_error = None
        html = ""

        try:
            html = self._fetch_page(url)
            client_ids = self._collect_client_ids(html, url)
            resolved, active_client_id = self._resolve_url(url, client_ids)
            kind = resolved.get("kind")
            if kind == "playlist":
                return self._extract_playlist(resolved, client_ids, active_client_id)
            return self._extract_track(resolved, client_ids, active_client_id)
        except Exception as exc:
            api_error = exc
            logger.warning("SoundCloud API extraction failed for %s, falling back to yt-dlp: %s", url, exc)

        try:
            return self._extract_with_yt_dlp(url)
        except Exception as yt_exc:
            if isinstance(yt_exc, ExtractorError):
                raise yt_exc
            if isinstance(api_error, ExtractorError):
                raise api_error
            raise ExtractorError("Failed to extract SoundCloud media", platform=self.PLATFORM_ID, url=url) from yt_exc

    def get_latest_content_ids(self, channel_url: str, limit: int = 10) -> list:
        effective_limit = max(1, min(int(limit or 10), 50))

        try:
            html = self._fetch_page(channel_url)
            client_ids = self._collect_client_ids(html, channel_url)
            resolved, active_client_id = self._resolve_url(channel_url, client_ids)
            user = resolved if resolved.get("kind") == "user" else (resolved.get("user") or {})
            user_id = user.get("id")
            if user_id:
                tracks = self._fetch_user_tracks(user_id, client_ids, active_client_id, effective_limit)
                urls = [
                    track.get("permalink_url")
                    for track in tracks
                    if isinstance(track, dict) and track.get("permalink_url")
                ]
                if urls:
                    return urls[:effective_limit]
        except Exception as exc:
            logger.warning("SoundCloud API latest content fallback for %s: %s", channel_url, exc)

        return self._latest_content_with_yt_dlp(channel_url, effective_limit)

    def _fetch_page(self, url: str) -> str:
        try:
            response = self.http_get(url)
            return response.text
        except Exception as exc:
            raise ExtractorError("Failed to load SoundCloud page", platform=self.PLATFORM_ID, url=url) from exc

    def _collect_client_ids(self, html: str, page_url: str) -> List[str]:
        candidates = []

        env_client_id = (os.environ.get("SOUNDCLOUD_CLIENT_ID") or "").strip()
        if env_client_id:
            candidates.append(env_client_id)

        candidates.extend(self._extract_client_ids_from_text(html))

        script_urls = self._extract_asset_script_urls(html, page_url)
        for script_url in script_urls[:8]:
            try:
                script_text = self.http_get(script_url).text
            except Exception:
                continue
            candidates.extend(self._extract_client_ids_from_text(script_text))

        candidates.extend(self._FALLBACK_CLIENT_IDS)
        return self._dedupe(candidates)

    def _extract_client_ids_from_text(self, text: str) -> List[str]:
        if not text:
            return []

        values = []
        for pattern in self._CLIENT_ID_PATTERNS:
            for match in re.finditer(pattern, text):
                client_id = (match.group(1) or "").strip()
                if client_id:
                    values.append(client_id)
        return values

    def _extract_asset_script_urls(self, html: str, page_url: str) -> List[str]:
        if not html:
            return []

        script_urls = []
        for match in re.finditer(r"<script[^>]+src=\"([^\"]+)\"", html):
            src = (match.group(1) or "").strip()
            if not src or "sndcdn.com/assets/" not in src:
                continue
            script_urls.append(urljoin(page_url, src))

        for match in re.finditer(r"<script[^>]+src='([^']+)'", html):
            src = (match.group(1) or "").strip()
            if not src or "sndcdn.com/assets/" not in src:
                continue
            script_urls.append(urljoin(page_url, src))

        return self._dedupe(script_urls)

    def _resolve_url(self, url: str, client_ids: List[str]) -> Tuple[dict, str]:
        if not client_ids:
            raise ExtractorError("SoundCloud client id not found", platform=self.PLATFORM_ID, url=url)

        last_error = None
        for client_id in client_ids:
            try:
                payload = self._soundcloud_api_get_json(
                    "https://api-v2.soundcloud.com/resolve",
                    params={"url": url, "client_id": client_id},
                    referer=url,
                )
                if isinstance(payload, dict) and payload.get("errors"):
                    last_error = ExtractorError(
                        "Failed to resolve SoundCloud URL",
                        platform=self.PLATFORM_ID,
                        url=url,
                        data={"errors": payload.get("errors")},
                    )
                    continue
                return payload, client_id
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status in {401, 403}:
                    continue
                if status == 404:
                    raise ExtractorError("SoundCloud URL not found", platform=self.PLATFORM_ID, url=url) from exc
            except Exception as exc:
                last_error = exc

        raise ExtractorError("Failed to resolve SoundCloud URL", platform=self.PLATFORM_ID, url=url) from last_error

    def _fetch_user_tracks(self, user_id: int, client_ids: List[str], active_client_id: str, limit: int) -> list:
        request_client_ids = self._dedupe([active_client_id] + list(client_ids))
        last_error = None

        for client_id in request_client_ids:
            try:
                payload = self._soundcloud_api_get_json(
                    f"https://api-v2.soundcloud.com/users/{user_id}/tracks",
                    params={"limit": limit, "client_id": client_id},
                    referer="https://soundcloud.com/",
                )
                if isinstance(payload, list):
                    return payload
                if isinstance(payload, dict) and isinstance(payload.get("collection"), list):
                    return payload.get("collection")
                return []
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status in {401, 403}:
                    continue
            except Exception as exc:
                last_error = exc

        raise ExtractorError("Failed to load SoundCloud user tracks", platform=self.PLATFORM_ID) from last_error

    def _fetch_track_streams(self, track_id: int, client_ids: List[str], active_client_id: str) -> dict:
        request_client_ids = self._dedupe([active_client_id] + list(client_ids))
        last_error = None

        for client_id in request_client_ids:
            try:
                return self._soundcloud_api_get_json(
                    f"https://api-v2.soundcloud.com/tracks/{track_id}/streams",
                    params={"client_id": client_id},
                    referer="https://soundcloud.com/",
                )
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status in {401, 403}:
                    continue
                if status == 404:
                    raise ExtractorError("SoundCloud track not found", platform=self.PLATFORM_ID) from exc
            except Exception as exc:
                last_error = exc

        raise ExtractorError("Failed to load SoundCloud streams", platform=self.PLATFORM_ID) from last_error

    def _extract_track(self, track: dict, client_ids: List[str], active_client_id: str) -> dict:
        track_id = track.get("id")
        if not track_id:
            raise ExtractorError("SoundCloud track id missing", platform=self.PLATFORM_ID)

        streams = self._fetch_track_streams(track_id, client_ids, active_client_id)

        qualities = []
        if streams.get("http_mp3_128_url"):
            qualities.append(
                {
                    "label": "audio_only",
                    "display_label": "audio_only",
                    "selector": "soundcloud_mp3_128",
                    "format_id": "http_mp3_128",
                    "url": streams.get("http_mp3_128_url"),
                    "size_bytes": None,
                    "codec": "mp3",
                    "bitrate": 128000,
                    "hdr": False,
                    "format": "mp3",
                    "has_audio": True,
                }
            )

        if streams.get("hls_mp3_128_url"):
            qualities.append(
                {
                    "label": "audio_hls",
                    "display_label": "audio_hls",
                    "selector": "soundcloud_hls_mp3_128",
                    "format_id": "hls_mp3_128",
                    "url": streams.get("hls_mp3_128_url"),
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": 128000,
                    "hdr": False,
                    "format": "m4a",
                    "is_hls": True,
                    "has_audio": True,
                }
            )

        if streams.get("hls_url"):
            qualities.append(
                {
                    "label": "audio_hls_fallback",
                    "display_label": "audio_hls_fallback",
                    "selector": "soundcloud_hls",
                    "format_id": "hls",
                    "url": streams.get("hls_url"),
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": "m4a",
                    "is_hls": True,
                    "has_audio": True,
                }
            )

        if not qualities:
            raise ExtractorError("No playable SoundCloud stream found", platform=self.PLATFORM_ID)

        artwork = track.get("artwork_url")
        if artwork:
            artwork = artwork.replace("-large", "-t500x500")

        upload_date = None
        created_at = track.get("created_at")
        if created_at:
            upload_date = created_at.split("T")[0]

        return {
            "title": track.get("title"),
            "author": (track.get("user") or {}).get("username"),
            "channel_id": None,
            "thumbnail": artwork,
            "duration": int((track.get("duration") or 0) / 1000),
            "view_count": track.get("playback_count"),
            "description": (track.get("description") or "")[:500] if track.get("description") else None,
            "upload_date": upload_date,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {},
            "platform": self.PLATFORM_ID,
        }

    def _extract_playlist(self, playlist: dict, client_ids: List[str], active_client_id: str) -> dict:
        tracks = playlist.get("tracks") or []
        first = tracks[0] if tracks else {}
        qualities = []

        for index, track in enumerate(tracks, start=1):
            track_id = track.get("id")
            track_title = (track.get("title") or "track").strip()

            try:
                streams = self._fetch_track_streams(track_id, client_ids, active_client_id) if track_id else {}
                stream_entry = self._pick_stream_entry(streams)
            except Exception:
                stream_entry = None

            stream_url = (stream_entry or {}).get("url")
            stream_format = (stream_entry or {}).get("format")
            stream_is_hls = bool((stream_entry or {}).get("is_hls"))

            if stream_url:
                qualities.append(
                    {
                        "label": f"track_{index}_{track_title}"[:80],
                        "display_label": f"Track {index}",
                        "selector": f"soundcloud_playlist_track_{index}",
                        "format_id": None,
                        "url": stream_url,
                        "size_bytes": None,
                        "codec": "mp3" if stream_format == "mp3" else None,
                        "bitrate": stream_entry.get("bitrate") if stream_entry else None,
                        "hdr": False,
                        "format": stream_format or "mp3",
                        "is_hls": stream_is_hls,
                        "has_audio": True,
                    }
                )

        if not qualities:
            raise ExtractorError("No downloadable tracks found in SoundCloud playlist", platform=self.PLATFORM_ID)

        duration_ms = playlist.get("duration")
        duration_seconds = None
        if duration_ms:
            try:
                duration_seconds = int(duration_ms / 1000)
            except Exception:
                duration_seconds = None

        thumbnail = playlist.get("artwork_url") or first.get("artwork_url")
        if thumbnail:
            thumbnail = thumbnail.replace("-large", "-t500x500")

        return {
            "title": playlist.get("title") or first.get("title"),
            "author": (playlist.get("user") or {}).get("username"),
            "channel_id": None,
            "thumbnail": thumbnail,
            "duration": duration_seconds,
            "view_count": playlist.get("playback_count"),
            "description": (playlist.get("description") or "")[:500] if playlist.get("description") else None,
            "upload_date": None,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {},
            "platform": self.PLATFORM_ID,
        }

    def _pick_stream_entry(self, streams: dict) -> dict:
        for key, format_name, is_hls, bitrate in self._STREAM_KEYS:
            value = streams.get(key)
            if not value:
                continue
            return {
                "url": value,
                "format": format_name,
                "is_hls": is_hls,
                "bitrate": bitrate,
            }
        return {}

    def _extract_with_yt_dlp(self, url: str) -> dict:
        headers = self.get_headers()
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "noplaylist": False,
            "format": "bestaudio/best",
            "socket_timeout": 30,
            "retries": 2,
            "fragment_retries": 2,
            "extractor_retries": 2,
            "skip_unavailable_fragments": True,
            "http_headers": {
                "User-Agent": headers.get("User-Agent"),
                "Accept-Language": headers.get("Accept-Language", "en-US,en;q=0.9"),
                "Referer": "https://soundcloud.com/",
            },
        }

        proxy_url = self._resolve_proxy_url()
        if proxy_url:
            ydl_opts["proxy"] = proxy_url

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise ExtractorError(str(exc), platform=self.PLATFORM_ID, url=url) from exc

        entries = info.get("entries")
        if entries:
            return self._extract_playlist_from_yt_dlp(info)
        return self._extract_track_from_yt_dlp(info)

    def _extract_track_from_yt_dlp(self, info: dict) -> dict:
        formats = info.get("formats") or []
        qualities = []
        seen = set()

        for fmt in formats:
            stream_url = fmt.get("url")
            if not stream_url:
                continue

            acodec = str(fmt.get("acodec") or "")
            vcodec = str(fmt.get("vcodec") or "")
            if vcodec and vcodec != "none":
                continue
            if acodec in {"", "none"}:
                continue

            format_id = str(fmt.get("format_id") or "")
            bitrate = fmt.get("abr") or fmt.get("tbr")
            bitrate_bps = None
            if bitrate:
                try:
                    bitrate_bps = int(float(bitrate) * 1000)
                except (TypeError, ValueError):
                    bitrate_bps = None

            selector = format_id or f"soundcloud_audio_{len(qualities) + 1}"
            if selector in seen:
                continue
            seen.add(selector)

            ext = (fmt.get("ext") or "mp3").lower()
            entry = {
                "label": "audio_only",
                "display_label": "audio_only",
                "selector": selector,
                "format_id": format_id or None,
                "url": stream_url,
                "size_bytes": fmt.get("filesize") or fmt.get("filesize_approx"),
                "codec": acodec,
                "bitrate": bitrate_bps,
                "hdr": False,
                "format": ext,
                "has_audio": True,
            }
            if ".m3u8" in stream_url:
                entry["is_hls"] = True
            qualities.append(entry)

        if not qualities:
            fallback_url = info.get("url")
            if fallback_url:
                fallback_entry = {
                    "label": "audio_only",
                    "display_label": "audio_only",
                    "selector": "soundcloud_audio_fallback",
                    "format_id": None,
                    "url": fallback_url,
                    "size_bytes": info.get("filesize") or info.get("filesize_approx"),
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": (info.get("ext") or "mp3").lower(),
                    "has_audio": True,
                }
                if ".m3u8" in fallback_url:
                    fallback_entry["is_hls"] = True
                qualities.append(fallback_entry)

        if not qualities:
            raise ExtractorError("No playable SoundCloud stream found", platform=self.PLATFORM_ID)

        qualities.sort(key=lambda item: item.get("bitrate") or 0, reverse=True)

        upload_date = None
        raw_date = info.get("upload_date")
        if raw_date:
            try:
                upload_date = datetime.strptime(raw_date, "%Y%m%d").strftime("%Y-%m-%d")
            except ValueError:
                upload_date = None

        return {
            "title": info.get("title") or "SoundCloud Track",
            "author": info.get("uploader") or info.get("artist"),
            "channel_id": info.get("channel_id"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "description": (info.get("description") or "")[:500] if info.get("description") else None,
            "upload_date": upload_date,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": any(item.get("is_hls") for item in qualities),
            "manifest_url": None,
            "headers_required": info.get("http_headers") or {},
            "platform": self.PLATFORM_ID,
        }

    def _extract_playlist_from_yt_dlp(self, info: dict) -> dict:
        qualities = []
        entries = info.get("entries") or []

        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                continue
            stream_url = entry.get("url")
            if not stream_url:
                stream_url = entry.get("webpage_url")
            if not stream_url:
                continue

            title = (entry.get("title") or "track").strip()
            qualities.append(
                {
                    "label": f"track_{index}_{title}"[:80],
                    "display_label": f"Track {index}",
                    "selector": f"soundcloud_playlist_track_{index}",
                    "format_id": None,
                    "url": stream_url,
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": (entry.get("ext") or "mp3").lower(),
                    "has_audio": True,
                    "is_hls": ".m3u8" in stream_url,
                }
            )

        if not qualities:
            raise ExtractorError("No downloadable tracks found in SoundCloud playlist", platform=self.PLATFORM_ID)

        duration = info.get("duration")
        if duration is None:
            duration = 0
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("duration"), (int, float)):
                    duration += int(entry.get("duration"))

        return {
            "title": info.get("title") or "SoundCloud Playlist",
            "author": info.get("uploader") or info.get("channel"),
            "channel_id": info.get("channel_id"),
            "thumbnail": info.get("thumbnail"),
            "duration": duration,
            "view_count": info.get("view_count"),
            "description": (info.get("description") or "")[:500] if info.get("description") else None,
            "upload_date": None,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": any(item.get("is_hls") for item in qualities),
            "manifest_url": None,
            "headers_required": info.get("http_headers") or {},
            "platform": self.PLATFORM_ID,
        }

    def _latest_content_with_yt_dlp(self, channel_url: str, limit: int) -> list:
        headers = self.get_headers()
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "noplaylist": False,
            "playlistend": limit,
            "socket_timeout": 20,
            "retries": 1,
            "fragment_retries": 1,
            "extractor_retries": 1,
            "http_headers": {
                "User-Agent": headers.get("User-Agent"),
                "Accept-Language": headers.get("Accept-Language", "en-US,en;q=0.9"),
                "Referer": "https://soundcloud.com/",
            },
        }

        proxy_url = self._resolve_proxy_url()
        if proxy_url:
            ydl_opts["proxy"] = proxy_url

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(channel_url, download=False)
        except yt_dlp.utils.DownloadError:
            return []

        entries = info.get("entries") or []
        urls = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            webpage_url = entry.get("webpage_url")
            if webpage_url:
                urls.append(webpage_url)
                continue
            stream_url = entry.get("url")
            if isinstance(stream_url, str) and stream_url.startswith("http"):
                urls.append(stream_url)

        return self._dedupe(urls)[:limit]

    def _soundcloud_api_get_json(self, endpoint: str, params: dict, referer: str = None):
        headers = self.get_headers().copy()
        headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://soundcloud.com",
                "Referer": referer or "https://soundcloud.com/",
            }
        )

        with self.create_http_client(
            headers=headers,
            timeout=30,
            follow_redirects=True,
        ) as client:
            response = client.get(endpoint, params=params)
            response.raise_for_status()
            return response.json()

    def _dedupe(self, values: list) -> list:
        seen = set()
        result = []
        for value in values:
            item = str(value or "").strip()
            if not item or item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result
