import json
import logging
import re
from urllib.parse import urlparse

import httpx

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class ImgurExtractor(BaseExtractor):
    PLATFORM_ID = "imgur"
    REQUIRES_HEADLESS = False
    REQUIRES_PROXY = False
    TEST_URL = "https://imgur.com/gallery/9BHBK"

    CLIENT_ID = "546c25a59c58ad7"

    def extract(self, url: str) -> dict:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        path = (parsed.path or "").strip()
        lowered_url = (url or "").lower()

        if host == "i.imgur.com":
            return self._extract_direct_imgur_url(url)

        if lowered_url.endswith(".gifv"):
            return self._extract_gifv_url(url)

        is_album = path.startswith("/a/") or path.startswith("/gallery/")
        imgur_id = self._extract_imgur_id(path)
        if not imgur_id:
            raise ExtractorError("Unsupported Imgur URL", platform=self.PLATFORM_ID, url=url)

        try:
            api_data = self.get_imgur_api_data(imgur_id, is_album=is_album)
        except ExtractorError:
            api_data = None

        if api_data:
            if is_album:
                album_data = api_data.get("album") or {}
                images_data = api_data.get("images") or album_data.get("images") or []
                if not album_data and not images_data:
                    raise ExtractorError(
                        "This Imgur image has been deleted or is private",
                        platform=self.PLATFORM_ID,
                        url=url,
                    )
                return self._build_album_media_info(url, album_data, images_data)

            image_data = api_data.get("image")
            if not image_data:
                raise ExtractorError(
                    "This Imgur image has been deleted or is private",
                    platform=self.PLATFORM_ID,
                    url=url,
                )
            return self._build_single_media_info(url, image_data)

        scraped = self.scrape_imgur_page(url)
        if not scraped:
            raise ExtractorError(
                "This Imgur image has been deleted or is private",
                platform=self.PLATFORM_ID,
                url=url,
            )

        if scraped.get("album"):
            return self._build_album_media_info(url, scraped.get("album") or {}, scraped.get("images") or [])

        if scraped.get("image"):
            return self._build_single_media_info(url, scraped.get("image"))

        raise ExtractorError(
            "This Imgur image has been deleted or is private",
            platform=self.PLATFORM_ID,
            url=url,
        )

    def get_imgur_api_data(self, imgur_id: str, is_album: bool = False):
        headers = {
            **self.get_headers(),
            "Authorization": f"Client-ID {self.CLIENT_ID}",
            "Accept": "application/json",
        }

        try:
            with self.create_http_client(headers=headers, timeout=20, follow_redirects=True) as client:
                if is_album:
                    album_resp = client.get(f"https://api.imgur.com/3/album/{imgur_id}")
                    images_resp = client.get(f"https://api.imgur.com/3/album/{imgur_id}/images")
                    if album_resp.status_code >= 400 or images_resp.status_code >= 400:
                        raise ExtractorError(
                            f"Imgur API request failed with status {album_resp.status_code}",
                            platform=self.PLATFORM_ID,
                            url=f"https://imgur.com/a/{imgur_id}",
                        )
                    album_json = album_resp.json()
                    images_json = images_resp.json()
                    if not album_json.get("success"):
                        return None
                    return {
                        "album": album_json.get("data") or {},
                        "images": images_json.get("data") or [],
                    }

                image_resp = client.get(f"https://api.imgur.com/3/image/{imgur_id}")
                if image_resp.status_code >= 400:
                    raise ExtractorError(
                        f"Imgur API request failed with status {image_resp.status_code}",
                        platform=self.PLATFORM_ID,
                        url=f"https://imgur.com/{imgur_id}",
                    )
                image_json = image_resp.json()
                if not image_json.get("success"):
                    return None
                return {"image": image_json.get("data") or {}}

        except httpx.HTTPError as exc:
            raise ExtractorError("Imgur API request failed", platform=self.PLATFORM_ID) from exc

    def scrape_imgur_page(self, url: str):
        try:
            response = self.http_get(url)
            html_content = response.text
        except Exception:
            return None

        parsed = self._extract_json_ld_payload(html_content)
        if parsed:
            if parsed.get("images"):
                return {"album": parsed, "images": parsed.get("images")}
            return {"image": parsed}

        for pattern in [
            r"window\.imageData\s*=\s*(\{.*?\})\s*;",
            r"window\.__NEXT_DATA__\s*=\s*(\{.*?\})\s*;",
            r"window\.imgurOpts\s*=\s*(\{.*?\})\s*;",
        ]:
            match = re.search(pattern, html_content, flags=re.IGNORECASE | re.DOTALL)
            if not match:
                continue
            payload = self._safe_json_loads(match.group(1))
            if not payload:
                continue
            extracted = self._extract_imgur_payload(payload)
            if extracted:
                return extracted

        direct_media = self._extract_direct_media_from_html(html_content)
        if direct_media:
            return {"image": direct_media}

        return None

    def build_qualities_for_single_image(self, image_data: dict) -> list:
        link = self._normalize_url(image_data.get("link") or image_data.get("url"))
        if not link:
            return []

        media_type = str(image_data.get("type") or "").lower()
        title = self._clean_text(image_data.get("title") or "")

        qualities = []

        is_animated = bool(image_data.get("animated")) or "gif" in media_type or link.lower().endswith(".gifv")
        if is_animated:
            mp4_url = self._to_mp4_url(image_data.get("mp4") or link)
            gif_url = self._to_gif_url(image_data.get("gifv") or link)

            qualities.append(
                {
                    "label": "Video (MP4)",
                    "url": mp4_url,
                    "size_bytes": self._to_int(image_data.get("size")),
                    "codec": "h264",
                    "bitrate": None,
                    "hdr": False,
                    "format": "mp4",
                }
            )

            qualities.append(
                {
                    "label": "Animated GIF",
                    "url": gif_url,
                    "size_bytes": None,
                    "codec": "gif",
                    "bitrate": None,
                    "hdr": False,
                    "format": "gif",
                }
            )
            return qualities

        ext = self._guess_extension(link)
        qualities.append(
            {
                "label": "Original",
                "url": link,
                "size_bytes": self._to_int(image_data.get("size")),
                "codec": None,
                "bitrate": None,
                "hdr": False,
                "format": ext,
            }
        )

        if "i.imgur.com/" in link:
            code_urls = self._build_imgur_size_urls(link)
            for label, variant_url in code_urls:
                qualities.append(
                    {
                        "label": label,
                        "url": variant_url,
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "format": self._guess_extension(variant_url),
                    }
                )

        if title:
            for quality in qualities:
                quality["image_title"] = title

        return qualities

    def build_qualities_for_album(self, album_data: dict, images_data: list) -> list:
        qualities = []
        image_items = images_data or album_data.get("images") or []
        image_urls = []

        count = 0
        for index, image_data in enumerate(image_items, start=1):
            link = self._normalize_url(image_data.get("link") or image_data.get("url"))
            if not link:
                continue
            count += 1
            image_urls.append(link)

            title = self._truncate(self._clean_text(image_data.get("title") or "Untitled"), 50)
            qualities.append(
                {
                    "label": f"Image {index}: {title}",
                    "url": link,
                    "size_bytes": self._to_int(image_data.get("size")),
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": self._guess_extension(link),
                }
            )

        if count > 0:
            first_image = qualities[0]
            qualities.insert(
                0,
                {
                    "label": f"All {count} images (original)",
                    "url": first_image.get("url"),
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": first_image.get("format"),
                    "collection_urls": image_urls,
                },
            )

        return qualities

    def _build_single_media_info(self, source_url: str, image_data: dict) -> dict:
        qualities = self.build_qualities_for_single_image(image_data)
        if not qualities:
            raise ExtractorError(
                "This Imgur image has been deleted or is private",
                platform=self.PLATFORM_ID,
                url=source_url,
            )

        return {
            "title": self._clean_text(image_data.get("title") or "Imgur Media"),
            "author": self._clean_text(image_data.get("account_url") or "Imgur"),
            "channel_id": image_data.get("id"),
            "thumbnail": self._normalize_url(image_data.get("link") or image_data.get("thumbnail")),
            "duration": self._to_int(image_data.get("duration")),
            "view_count": self._to_int(image_data.get("views")),
            "description": self._truncate(self._clean_text(image_data.get("description")), 500),
            "upload_date": None,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {},
        }

    def _build_album_media_info(self, source_url: str, album_data: dict, images_data: list) -> dict:
        qualities = self.build_qualities_for_album(album_data, images_data)
        if not qualities:
            raise ExtractorError(
                "This Imgur image has been deleted or is private",
                platform=self.PLATFORM_ID,
                url=source_url,
            )

        return {
            "title": self._clean_text(album_data.get("title") or "Imgur Album"),
            "author": self._clean_text(album_data.get("account_url") or "Imgur"),
            "channel_id": album_data.get("id"),
            "thumbnail": self._normalize_url(
                album_data.get("cover") and f"https://i.imgur.com/{album_data.get('cover')}.jpg"
            ),
            "duration": None,
            "view_count": self._to_int(album_data.get("views")),
            "description": self._truncate(self._clean_text(album_data.get("description")), 500),
            "upload_date": None,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {},
        }

    def _extract_direct_imgur_url(self, url: str) -> dict:
        normalized = self._normalize_url(url)
        if normalized.lower().endswith(".gifv"):
            return self._extract_gifv_url(normalized)

        return {
            "title": "Imgur Direct Media",
            "author": "Imgur",
            "channel_id": None,
            "thumbnail": normalized,
            "duration": None,
            "view_count": None,
            "description": None,
            "upload_date": None,
            "qualities": [
                {
                    "label": "Original",
                    "url": normalized,
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": self._guess_extension(normalized),
                }
            ],
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {},
        }

    def _extract_gifv_url(self, url: str) -> dict:
        mp4_url = self._to_mp4_url(url)
        gif_url = self._to_gif_url(url)
        return {
            "title": "Imgur GIFV Media",
            "author": "Imgur",
            "channel_id": None,
            "thumbnail": gif_url,
            "duration": None,
            "view_count": None,
            "description": None,
            "upload_date": None,
            "qualities": [
                {
                    "label": "Video (MP4)",
                    "url": mp4_url,
                    "size_bytes": None,
                    "codec": "h264",
                    "bitrate": None,
                    "hdr": False,
                    "format": "mp4",
                },
                {
                    "label": "Animated GIF",
                    "url": gif_url,
                    "size_bytes": None,
                    "codec": "gif",
                    "bitrate": None,
                    "hdr": False,
                    "format": "gif",
                },
            ],
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {},
        }

    def _extract_imgur_payload(self, payload):
        if isinstance(payload, dict):
            if payload.get("album") and isinstance(payload.get("album"), dict):
                album = payload.get("album")
                images = album.get("images") or payload.get("images") or []
                return {"album": album, "images": images}

            if payload.get("image") and isinstance(payload.get("image"), dict):
                return {"image": payload.get("image")}

            image_candidate = self._find_image_node(payload)
            if image_candidate:
                return {"image": image_candidate}

            album_candidate = self._find_album_node(payload)
            if album_candidate:
                return {
                    "album": album_candidate,
                    "images": album_candidate.get("images") or [],
                }

        return None

    def _extract_json_ld_payload(self, html_content: str):
        block = re.search(
            r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
            html_content,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not block:
            return None

        payload = self._safe_json_loads(block.group(1))
        if not payload:
            return None

        if isinstance(payload, dict) and payload.get("@type") in {"ImageObject", "MediaObject"}:
            return {
                "title": payload.get("name"),
                "description": payload.get("description"),
                "link": payload.get("contentUrl") or payload.get("url") or payload.get("thumbnailUrl"),
            }

        return None

    def _extract_direct_media_from_html(self, html_content: str):
        for pattern in [
            r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"'](.*?)[\"']",
            r"https?://i\.imgur\.com/[^\s\"']+\.(?:jpg|jpeg|png|gif|gifv|mp4|webm)",
        ]:
            match = re.search(pattern, html_content, flags=re.IGNORECASE)
            if not match:
                continue
            media_url = match.group(1) if match.lastindex else match.group(0)
            return {
                "title": self._extract_meta_content(html_content, "og:title"),
                "description": self._extract_meta_content(html_content, "og:description"),
                "link": media_url,
            }
        return None

    def _find_image_node(self, payload):
        def walk(node):
            if isinstance(node, dict):
                link = node.get("link") or node.get("url")
                if isinstance(link, str) and "imgur.com" in link and re.search(
                    r"\.(jpg|jpeg|png|gif|gifv|mp4|webm)(\?|$)",
                    link,
                    flags=re.IGNORECASE,
                ):
                    return node
                for value in node.values():
                    result = walk(value)
                    if result:
                        return result
            elif isinstance(node, list):
                for item in node:
                    result = walk(item)
                    if result:
                        return result
            return None

        return walk(payload)

    def _find_album_node(self, payload):
        def walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("images"), list) and node.get("images"):
                    if node.get("is_album") or node.get("cover") or node.get("album_images"):
                        return node
                for value in node.values():
                    result = walk(value)
                    if result:
                        return result
            elif isinstance(node, list):
                for item in node:
                    result = walk(item)
                    if result:
                        return result
            return None

        return walk(payload)

    def _extract_imgur_id(self, path: str) -> str:
        cleaned = (path or "").strip("/")
        if not cleaned:
            return ""

        parts = cleaned.split("/")
        if parts[0] in {"a", "gallery"} and len(parts) > 1:
            return re.sub(r"[^a-zA-Z0-9]", "", parts[1])

        first = parts[0]
        first = first.split(".")[0]
        return re.sub(r"[^a-zA-Z0-9]", "", first)

    def _to_mp4_url(self, url: str) -> str:
        value = self._normalize_url(url)
        value = re.sub(r"\.gifv(\?|$)", r".mp4\1", value, flags=re.IGNORECASE)
        if value.lower().endswith(".gif"):
            return value[:-4] + ".mp4"
        return value

    def _to_gif_url(self, url: str) -> str:
        value = self._normalize_url(url)
        value = re.sub(r"\.gifv(\?|$)", r".gif\1", value, flags=re.IGNORECASE)
        if value.lower().endswith(".mp4"):
            return value[:-4] + ".gif"
        return value

    def _build_imgur_size_urls(self, original_url: str):
        parsed = urlparse(original_url)
        path = parsed.path or ""
        match = re.search(r"/([A-Za-z0-9]+)(\.[a-zA-Z0-9]+)$", path)
        if not match:
            return []

        image_hash = match.group(1)
        extension = match.group(2)
        base = f"{parsed.scheme}://{parsed.netloc}/{image_hash}"

        variants = [
            ("Huge", f"{base}h{extension}"),
            ("Large", f"{base}l{extension}"),
            ("Medium", f"{base}m{extension}"),
        ]

        return variants

    def _extract_meta_content(self, html_content: str, key: str) -> str:
        pattern = (
            r"<meta[^>]+(?:property|name)=[\"']"
            + re.escape(key)
            + r"[\"'][^>]+content=[\"'](.*?)[\"'][^>]*>"
        )
        match = re.search(pattern, html_content, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return self._clean_text(match.group(1))

    def _normalize_url(self, value: str) -> str:
        if not value:
            return ""
        cleaned = str(value).strip().replace("\\/", "/")
        if cleaned.startswith("//"):
            cleaned = "https:" + cleaned
        return cleaned

    def _guess_extension(self, url: str) -> str:
        lowered = (url or "").lower()
        for ext in ["mp4", "webm", "gif", "png", "jpeg", "jpg"]:
            if f".{ext}" in lowered:
                if ext == "jpeg":
                    return "jpg"
                return ext
        return "jpg"

    def _to_int(self, value):
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _safe_json_loads(self, value: str):
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    def _clean_text(self, value) -> str:
        if value is None:
            return ""
        return re.sub(r"\s+", " ", str(value)).strip()

    def _truncate(self, value: str, length: int) -> str:
        text = self._clean_text(value)
        if len(text) <= length:
            return text
        return text[:length].rstrip() + "..."
