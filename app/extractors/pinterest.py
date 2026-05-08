import json
import logging
import re
from html import unescape
from urllib.parse import quote, urlparse

import httpx

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class PinterestExtractor(BaseExtractor):
    PLATFORM_ID = "pinterest"
    REQUIRES_HEADLESS = False
    REQUIRES_PROXY = False
    TEST_URL = "https://www.pinterest.com/pin/123456789012345678/"

    def extract(self, url: str) -> dict:
        pin_id = self._extract_pin_id(url)
        pin = self._fetch_pin_data(pin_id, source_url=url)

        title = pin.get("description") or (pin.get("board") or {}).get("name") or "Pinterest Pin"
        author = (pin.get("pinner") or {}).get("username")
        images = pin.get("images") or {}
        thumbnail = (images.get("736x") or images.get("orig") or {}).get("url")

        qualities = []
        if pin.get("videos"):
            videos = pin.get("videos") or {}
            for key, item in videos.items():
                url_value = item.get("url")
                if url_value:
                    qualities.append(
                        {
                            "label": key,
                            "url": url_value,
                            "size_bytes": None,
                            "codec": None,
                            "bitrate": None,
                            "hdr": False,
                            "format": "mp4",
                        }
                    )
            content_type = "video"
        else:
            content_type = "image"
            for key, item in images.items():
                if not isinstance(item, dict):
                    continue
                url_value = item.get("url")
                if not url_value:
                    continue
                qualities.append(
                    {
                        "label": key,
                        "url": url_value,
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "format": "jpg",
                    }
                )

        if not qualities:
            raise ExtractorError("Pinterest media not found", platform=self.PLATFORM_ID, url=url)

        return {
            "title": title,
            "author": author,
            "channel_id": None,
            "thumbnail": thumbnail,
            "duration": None,
            "view_count": None,
            "description": pin.get("description"),
            "upload_date": None,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {},
        }

    def _extract_pin_id(self, url: str) -> str:
        pin_id = self._extract_pin_id_from_text(url)
        if pin_id:
            return pin_id

        resolved_url = self._resolve_pin_url(url)
        pin_id = self._extract_pin_id_from_text(resolved_url)
        if pin_id:
            return pin_id

        pin_id = self._extract_pin_id_from_page(resolved_url)
        if pin_id:
            return pin_id

        raise ExtractorError("Invalid Pinterest URL", platform=self.PLATFORM_ID, url=url)

    def _resolve_pin_url(self, url: str) -> str:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if hostname.startswith("www."):
            hostname = hostname[4:]
        if hostname != "pin.it":
            return url

        try:
            with self.create_http_client(
                timeout=20,
                follow_redirects=True,
                headers=self.get_headers(),
            ) as client:
                response = client.get(url)
                response.raise_for_status()
                return str(response.url)
        except httpx.HTTPError:
            return url

    def _extract_pin_id_from_page(self, url: str) -> str | None:
        try:
            html = self.http_get(url).text
        except ExtractorError:
            return None
        return self._extract_pin_id_from_text(html)

    def _extract_pin_id_from_text(self, value: str) -> str | None:
        patterns = [
            r"/pin/(\d+)",
            r"\\/pin\\/(\d+)",
            r"[?&]pin_id=(\d+)",
            r"pinterest://pin/(\d+)",
            r'"pin_id"\s*:\s*"?(\d+)"?',
            r'"entityId"\s*:\s*"?(\d+)"?',
            r'"id"\s*:\s*"?(\d{6,})"?\s*,\s*"__typename"\s*:\s*"Pin"',
        ]
        for pattern in patterns:
            match = re.search(pattern, value or "", re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _fetch_pin_data(self, pin_id: str, source_url: str | None = None) -> dict:
        pin = self._fetch_pin_data_from_api(pin_id)
        if pin:
            return pin

        pin = self._fetch_pin_data_from_page(pin_id, source_url=source_url)
        if pin:
            return pin

        raise ExtractorError("Failed to fetch Pinterest data", platform=self.PLATFORM_ID, url=source_url)

    def _fetch_pin_data_from_api(self, pin_id: str) -> dict | None:
        data = {
            "options": {
                "id": pin_id,
                "field_set_key": "detailed",
            }
        }
        url = (
            "https://www.pinterest.com/resource/PinResource/get/?source_url="
            f"/pin/{pin_id}/&data={quote(json.dumps(data, separators=(",", ":")))}"
        )

        headers = self.get_headers()
        headers.update(
            {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "X-Pinterest-Source-Url": f"/pin/{pin_id}/",
                "Referer": f"https://www.pinterest.com/pin/{pin_id}/",
            }
        )

        try:
            with self.create_http_client(timeout=20, follow_redirects=True, headers=headers) as client:
                response = client.get(url)
            if response.status_code in {401, 403, 429}:
                logger.info("Pinterest API blocked with status %s for pin %s", response.status_code, pin_id)
                return None
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("Pinterest API request failed for pin %s: %s", pin_id, exc)
            return None

        resource = payload.get("resource_response", {}) if isinstance(payload, dict) else {}
        pin = resource.get("data") if isinstance(resource, dict) else None
        if isinstance(pin, dict):
            return pin
        return None

    def _fetch_pin_data_from_page(self, pin_id: str, source_url: str | None = None) -> dict | None:
        candidate_urls = []

        if source_url:
            candidate_urls.append(self._resolve_pin_url(source_url))

        canonical_url = f"https://www.pinterest.com/pin/{pin_id}/"
        if canonical_url not in candidate_urls:
            candidate_urls.append(canonical_url)

        for page_url in candidate_urls:
            try:
                html = self.http_get(page_url).text
            except ExtractorError:
                continue

            pin = self._extract_pin_from_html(html, pin_id)
            if pin:
                return pin

        return None

    def _extract_pin_from_html(self, html: str, pin_id: str) -> dict | None:
        markers = [
            "__PWS_DATA__",
            "__PWS_INITIAL_PROPS__",
            "__INITIAL_STATE__",
            "initialReduxState",
        ]

        for marker in markers:
            for raw_json in self._extract_json_objects_for_marker(html, marker):
                try:
                    payload = json.loads(raw_json)
                except json.JSONDecodeError:
                    continue
                pin = self._find_pin_in_payload(payload, pin_id)
                if pin:
                    return pin

        ld_json_pin = self._extract_pin_from_ld_json(html, pin_id)
        if ld_json_pin:
            return ld_json_pin

        media_pin = self._extract_pin_from_media_urls(html, pin_id)
        if media_pin:
            return media_pin

        return None

    def _extract_json_objects_for_marker(self, html: str, marker: str) -> list[str]:
        objects = []
        start = 0

        while True:
            marker_index = html.find(marker, start)
            if marker_index < 0:
                break

            brace_index = html.find("{", marker_index)
            if brace_index < 0:
                break

            json_blob = self._extract_balanced_json_object(html, brace_index)
            if json_blob:
                objects.append(json_blob)
                start = brace_index + len(json_blob)
            else:
                start = marker_index + len(marker)

        return objects

    def _extract_balanced_json_object(self, value: str, start_index: int) -> str | None:
        if start_index < 0 or start_index >= len(value) or value[start_index] != "{":
            return None

        depth = 0
        in_string = False
        escaped = False

        for index in range(start_index, len(value)):
            char = value[index]
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
                continue
            if char == "}":
                depth -= 1
                if depth == 0:
                    return value[start_index : index + 1]

        return None

    def _find_pin_in_payload(self, payload, pin_id: str) -> dict | None:
        wanted = str(pin_id)
        stack = [payload]

        while stack:
            node = stack.pop()
            if isinstance(node, list):
                for item in node:
                    if isinstance(item, (dict, list)):
                        stack.append(item)
                continue

            if not isinstance(node, dict):
                continue

            node_id = str(
                node.get("id")
                or node.get("pin_id")
                or node.get("pinId")
                or node.get("entityId")
                or ""
            )
            typename = str(node.get("__typename") or node.get("type") or "").lower()
            has_media = bool(node.get("images") or node.get("videos") or node.get("video_list") or node.get("story_pin_data"))

            if node_id == wanted and (has_media or "pin" in typename):
                return node

            resource_response = node.get("resource_response")
            if isinstance(resource_response, dict):
                data = resource_response.get("data")
                if isinstance(data, dict) and str(data.get("id") or "") == wanted:
                    return data

            for child in node.values():
                if isinstance(child, (dict, list)):
                    stack.append(child)

        return None

    def _extract_pin_from_ld_json(self, html: str, pin_id: str) -> dict | None:
        pattern = r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>"
        for match in re.finditer(pattern, html, re.IGNORECASE | re.DOTALL):
            raw = unescape((match.group(1) or "").strip())
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue

            blocks = payload if isinstance(payload, list) else [payload]
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                image_value = block.get("image")
                if isinstance(image_value, list):
                    image_url = next((item for item in image_value if isinstance(item, str) and item.startswith("http")), None)
                elif isinstance(image_value, str):
                    image_url = image_value
                else:
                    image_url = None

                content_url = block.get("contentUrl") if isinstance(block.get("contentUrl"), str) else None
                if not image_url and not content_url:
                    continue

                images = {"orig": {"url": image_url}} if image_url else {}
                videos = {"original": {"url": content_url}} if content_url else {}

                return {
                    "id": pin_id,
                    "description": block.get("description") or block.get("name"),
                    "images": images,
                    "videos": videos,
                    "pinner": {"username": block.get("author", {}).get("name") if isinstance(block.get("author"), dict) else None},
                }

        return None

    def _extract_pin_from_media_urls(self, html: str, pin_id: str) -> dict | None:
        video_urls = self._unique_urls(
            re.findall(r"https://v\d+\.pinimg\.com/[^\"'\s<>]+\.mp4[^\"'\s<>]*", html, re.IGNORECASE)
            + re.findall(r"https://v\.pinimg\.com/[^\"'\s<>]+\.mp4[^\"'\s<>]*", html, re.IGNORECASE)
        )
        image_urls = self._unique_urls(re.findall(r"https://i\.pinimg\.com/[^\"'\s<>]+", html, re.IGNORECASE))

        if not video_urls and not image_urls:
            return None

        images = {}
        for index, image_url in enumerate(image_urls, start=1):
            label = self._label_for_image_url(image_url, index)
            images[label] = {"url": image_url}
        if image_urls and "orig" not in images:
            images["orig"] = {"url": image_urls[0]}

        videos = {}
        for index, video_url in enumerate(video_urls, start=1):
            videos[f"video_{index}"] = {"url": video_url}

        return {
            "id": pin_id,
            "description": self._extract_meta_content(html, "og:description"),
            "images": images,
            "videos": videos,
            "pinner": {"username": None},
            "board": {"name": self._extract_meta_content(html, "og:title")},
        }

    def _label_for_image_url(self, url: str, index: int) -> str:
        if "/originals/" in url:
            return "orig"
        size_match = re.search(r"/(\d+x)/", url)
        if size_match:
            return size_match.group(1)
        return f"image_{index}"

    def _extract_meta_content(self, html: str, key: str) -> str | None:
        pattern = rf"<meta[^>]+(?:property|name)=[\"']{re.escape(key)}[\"'][^>]+content=[\"']([^\"']+)[\"']"
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return unescape(match.group(1))
        return None

    def _unique_urls(self, urls: list[str]) -> list[str]:
        unique = []
        seen = set()
        for url in urls:
            normalized = unescape((url or "").replace("\\/", "/"))
            if not normalized.startswith("http"):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append(normalized)
        return unique
