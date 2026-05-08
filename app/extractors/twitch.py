import logging
import re
from urllib.parse import quote

import httpx
import m3u8

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class TwitchExtractor(BaseExtractor):
    PLATFORM_ID = "twitch"
    REQUIRES_HEADLESS = False
    REQUIRES_PROXY = False
    TEST_URL = "https://www.twitch.tv/videos/1234567890"

    _CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
    _MANIFEST_HOSTS = ("usher.ttvnw.net", "usher.twitch.tv", "usher.twitchapps.com")
    _MANIFEST_FETCH_RETRIES = 2
    _GQL_RETRIES = 3

    def extract(self, url: str) -> dict:
        if "clips.twitch.tv" in url or "/clip/" in url:
            return self._extract_clip_with_fallback(url)
        return self._extract_vod(url)

    def _extract_vod(self, url: str) -> dict:
        match = re.search(r"/videos/(\d+)", url)
        if not match:
            raise ExtractorError("Invalid Twitch VOD URL", platform=self.PLATFORM_ID, url=url)
        video_id = match.group(1)

        query = (
            "query { video(id: \""
            + video_id
            + "\") { title lengthSeconds viewCount createdAt owner { displayName } previewThumbnailURL } }"
        )
        metadata = self._gql_query(query)
        video = metadata.get("data", {}).get("video") or {}

        token_query = (
            "query { videoPlaybackAccessToken(id: \""
            + video_id
            + "\", params: {platform: \"web\", playerBackend: \"mediaplayer\", playerType: \"site\"}) "
            + "{ signature value } }"
        )
        token_data = self._gql_query(token_query)
        access = token_data.get("data", {}).get("videoPlaybackAccessToken") or {}
        signature = access.get("signature")
        token = access.get("value")
        if not signature or not token:
            raise ExtractorError("Unable to access Twitch stream", platform=self.PLATFORM_ID, url=url)

        manifest_urls = self._build_manifest_urls(video_id, signature, token)
        manifest_headers = self._manifest_headers(url)
        playlist, manifest_url = self._load_playlist(manifest_urls, manifest_headers, url)
        qualities = self._build_qualities(playlist, manifest_url, url)

        return {
            "title": video.get("title") or "Twitch Video",
            "author": (video.get("owner") or {}).get("displayName"),
            "channel_id": None,
            "thumbnail": video.get("previewThumbnailURL"),
            "duration": video.get("lengthSeconds"),
            "view_count": video.get("viewCount"),
            "description": None,
            "upload_date": (video.get("createdAt") or "")[:10] if video.get("createdAt") else None,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": True,
            "manifest_url": manifest_url,
            "headers_required": {
                "Referer": url,
                "Origin": "https://www.twitch.tv",
                "User-Agent": manifest_headers.get("User-Agent"),
            },
        }

    def _extract_clip_with_fallback(self, url: str) -> dict:
        last_error = None
        slug = None
        try:
            slug = self._extract_clip_slug(url)
        except Exception as exc:
            logger.warning("Failed to extract Twitch clip slug for %s: %s", url, exc)
            last_error = exc

        if slug:
            try:
                return self._extract_clip(url, slug)
            except Exception as exc:
                logger.warning("Twitch GQL clip extraction failed for %s: %s", url, exc)
                last_error = exc

        try:
            return self._extract_clip_with_yt_dlp(url)
        except Exception as exc:
            logger.warning("Twitch yt-dlp clip extraction failed for %s: %s", url, exc)
            if isinstance(exc, ExtractorError):
                raise
            if isinstance(last_error, ExtractorError):
                raise last_error
            raise ExtractorError("Failed to extract Twitch clip", platform=self.PLATFORM_ID, url=url) from exc

    def _extract_clip(self, url: str, slug: str = None) -> dict:
        if not slug:
            slug = self._extract_clip_slug(url)

        last_error = None
        for attempt in range(1, self._GQL_RETRIES + 1):
            try:
                clip_query = (
                    "query { clip(slug: \""
                    + slug
                    + "\") { title durationSeconds creator { displayName } thumbnailURL videoQualities { quality sourceURL } } }"
                )
                clip_data = self._gql_query(clip_query)
                clip = clip_data.get("data", {}).get("clip")
                
                if clip is None:
                    raise ValueError("Clip not found in Twitch API response")
                
                clip = clip or {}
                qualities = []
                for item in clip.get("videoQualities") or []:
                    url_value = item.get("sourceURL")
                    quality_value = item.get("quality")
                    if url_value and quality_value:
                        qualities.append(
                            {
                                "label": quality_value or "original",
                                "url": url_value,
                                "size_bytes": None,
                                "codec": None,
                                "bitrate": None,
                                "hdr": False,
                                "format": "mp4",
                            }
                        )

                if not qualities:
                    raise ValueError("No video qualities available in Twitch clip response")

                return {
                    "title": clip.get("title") or "Twitch Clip",
                    "author": (clip.get("creator") or {}).get("displayName"),
                    "channel_id": None,
                    "thumbnail": clip.get("thumbnailURL"),
                    "duration": clip.get("durationSeconds"),
                    "view_count": None,
                    "description": None,
                    "upload_date": None,
                    "qualities": qualities,
                    "subtitles": [],
                    "chapters": [],
                    "is_hls": False,
                    "manifest_url": None,
                    "headers_required": {"Referer": url},
                }
            except Exception as exc:
                last_error = exc
                logger.warning("Twitch clip GQL extraction failed (attempt %s/%s): %s", attempt, self._GQL_RETRIES, exc)

        raise ExtractorError("Unable to access Twitch clip", platform=self.PLATFORM_ID, url=url) from last_error

    def _extract_clip_with_yt_dlp(self, url: str) -> dict:
        try:
            import yt_dlp
        except ImportError:
            raise ExtractorError("yt-dlp not available for Twitch clip fallback", platform=self.PLATFORM_ID, url=url)

        headers = self.get_headers()
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "format": "best",
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
        except Exception as exc:
            raise ExtractorError("yt-dlp failed to extract Twitch clip", platform=self.PLATFORM_ID, url=url) from exc

        formats = info.get("formats") or []
        qualities = []
        seen_urls = set()

        for index, fmt in enumerate(formats):
            if not fmt.get("url"):
                continue

            media_url = fmt.get("url")
            if media_url in seen_urls:
                continue
            seen_urls.add(media_url)

            height = fmt.get("height")
            label = self._label_for_height(height) if height else "original"
            bitrate = fmt.get("tbr") or fmt.get("abr")
            bitrate_bps = None
            if bitrate:
                try:
                    bitrate_bps = int(float(bitrate) * 1000)
                except (TypeError, ValueError):
                    bitrate_bps = None

            qualities.append(
                {
                    "label": label,
                    "url": media_url,
                    "size_bytes": fmt.get("filesize") or fmt.get("filesize_approx"),
                    "codec": fmt.get("vcodec") or fmt.get("acodec"),
                    "bitrate": bitrate_bps,
                    "hdr": False,
                    "format": fmt.get("ext") or "mp4",
                }
            )

        if not qualities:
            raise ExtractorError("yt-dlp found no playable qualities for Twitch clip", platform=self.PLATFORM_ID, url=url)

        qualities = self._sort_qualities(qualities)

        return {
            "title": info.get("title") or "Twitch Clip",
            "author": info.get("uploader"),
            "channel_id": None,
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "description": None,
            "upload_date": None,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {"Referer": url},
        }

    def _get_proxy_url(self):
        if isinstance(self.proxy, dict):
            return self.proxy.get("https://") or self.proxy.get("http://")
        if isinstance(self.proxy, str):
            return self.proxy
        return None

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

        if label in {"audio_only", "audio", "audio only"}:
            return (1, 0, -bitrate)

        height = self._height_from_label(label)
        return (0, -height, -bitrate)

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


    def _extract_clip_slug(self, url):
        match = re.search(r"clips\.twitch\.tv/([a-zA-Z0-9_-]+)", url)
        if match:
            return match.group(1)
        match = re.search(r"/clip/([a-zA-Z0-9_-]+)", url)
        if match:
            return match.group(1)
        raise ExtractorError("Invalid Twitch clip URL", platform=self.PLATFORM_ID, url=url)

    def _build_manifest_urls(self, video_id: str, signature: str, token: str) -> list:
        encoded_token = quote(token, safe="")
        query = f"sig={signature}&token={encoded_token}&allow_source=true&allow_audio_only=true&fast_bread=true"
        urls = []
        for host in self._MANIFEST_HOSTS:
            urls.append(f"https://{host}/vod/{video_id}.m3u8?{query}")
            urls.append(f"https://{host}/vod/{video_id}?{query}")
        return urls

    def _manifest_headers(self, referer_url: str) -> dict:
        headers = self.get_headers()
        headers.update(
            {
                "Referer": referer_url,
                "Origin": "https://www.twitch.tv",
                "Accept": "application/vnd.apple.mpegurl,application/x-mpegURL,*/*",
            }
        )
        return headers

    def _load_playlist(self, manifest_urls: list, headers: dict, source_url: str):
        errors = []
        for manifest_url in manifest_urls:
            for attempt in range(1, self._MANIFEST_FETCH_RETRIES + 1):
                try:
                    response = self.http_get(manifest_url, headers=headers, timeout=20)
                    playlist = m3u8.loads(response.text, uri=manifest_url)
                    if not playlist:
                        raise ValueError("Empty Twitch manifest response")
                    if not playlist.playlists and not playlist.segments:
                        raise ValueError("Twitch manifest contains no stream entries")
                    return playlist, manifest_url
                except Exception as exc:
                    logger.warning(
                        "Twitch manifest request failed for %s (attempt %s/%s): %s",
                        manifest_url,
                        attempt,
                        self._MANIFEST_FETCH_RETRIES,
                        exc,
                    )
                    errors.append(f"{manifest_url} attempt {attempt}: {exc}")

        raise ExtractorError(
            "Failed to load Twitch playlist",
            platform=self.PLATFORM_ID,
            url=source_url,
            data={"attempts": errors[-3:]},
        )

    def _build_qualities(self, playlist, manifest_url: str, source_url: str) -> list:
        qualities = []
        seen_urls = set()

        for variant in playlist.playlists or []:
            variant_url = variant.absolute_uri or variant.uri
            if not variant_url or variant_url in seen_urls:
                continue

            seen_urls.add(variant_url)
            stream = variant.stream_info
            resolution = stream.resolution if stream else None
            label = "source"
            if resolution and len(resolution) == 2:
                label = f"{resolution[1]}p"
                frame_rate = stream.frame_rate if stream else None
                if frame_rate and frame_rate >= 50:
                    label = f"{label}60"

            qualities.append(
                {
                    "label": label,
                    "url": variant_url,
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": stream.bandwidth if stream else None,
                    "hdr": False,
                    "format": "m3u8",
                }
            )

        if not qualities and playlist.segments:
            qualities.append(
                {
                    "label": "source",
                    "url": manifest_url,
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": "m3u8",
                }
            )

        if not qualities:
            raise ExtractorError("No Twitch stream qualities found", platform=self.PLATFORM_ID, url=source_url)

        return qualities

    def _gql_query(self, query):
        payload = {"query": query}
        headers = {"Client-ID": self._CLIENT_ID}
        last_error = None
        for attempt in range(1, self._GQL_RETRIES + 1):
            try:
                with self.create_http_client(timeout=30, headers=headers) as client:
                    response = client.post("https://gql.twitch.tv/gql", json=payload)
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("Twitch API request failed (attempt %s/%s): %s", attempt, self._GQL_RETRIES, exc)

        raise ExtractorError("Twitch API request failed", platform=self.PLATFORM_ID) from last_error
