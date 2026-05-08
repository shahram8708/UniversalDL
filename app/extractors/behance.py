import json
import logging
import re
from urllib.parse import urlparse

import httpx

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class BehanceExtractor(BaseExtractor):
    PLATFORM_ID = "behance"
    REQUIRES_HEADLESS = False
    REQUIRES_PROXY = False
    TEST_URL = "https://www.behance.net/gallery/188756185/Project-Name"

    def extract(self, url: str) -> dict:
        project_id = self._extract_project_id(url)

        html_content = ""
        try:
            html_content = self._fetch_page_html(url)
        except ExtractorError:
            html_content = ""

        project_data = self._extract_project_data(html_content, project_id) if html_content else None

        if not project_data and project_id:
            project_data = self.fetch_project_api(project_id, url)

        fallback_qualities = self._extract_fallback_qualities(html_content, project_data)

        if not project_data and not fallback_qualities:
            raise ExtractorError(
                "Unable to read Behance project metadata",
                platform=self.PLATFORM_ID,
                url=url,
            )

        data = project_data or {}
        meta_title = self._extract_meta_content(html_content, "og:title")
        meta_description = self._extract_meta_content(html_content, "og:description")
        meta_thumbnail = self._extract_meta_content(html_content, "og:image") or self._extract_meta_content(
            html_content, "twitter:image"
        )
        meta_author = self._extract_meta_content(html_content, "author") or self._extract_meta_content(
            html_content, "og:site_name"
        )

        title = self._clean_text(data.get("name") or data.get("title") or meta_title) or "Behance Project"
        author = self._extract_author(data) or self._clean_text(meta_author)
        thumbnail = self._extract_thumbnail(data) or meta_thumbnail
        description = self._truncate(
            self._clean_text(data.get("description") or meta_description),
            500,
        )

        modules = data.get("modules") or []
        qualities = self.build_qualities_from_project(modules) if modules else []

        if not qualities:
            qualities = fallback_qualities

        if not qualities:
            raise ExtractorError(
                "This Behance project contains only text — no downloadable media",
                platform=self.PLATFORM_ID,
                url=url,
            )

        return {
            "title": title,
            "author": author,
            "channel_id": str(data.get("id") or project_id or url),
            "thumbnail": thumbnail,
            "duration": None,
            "view_count": data.get("stats", {}).get("views") if isinstance(data.get("stats"), dict) else None,
            "description": description,
            "upload_date": None,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {},
        }

    def fetch_project_api(self, project_id: str, page_url: str = ""):
        endpoints = [
            f"https://www.behance.net/v2/projects/{project_id}",
            f"https://www.behance.net/v2/projects/{project_id}?client_id=BehanceSDK",
            f"https://www.behance.net/v2/projects/{project_id}?client_id=BehanceSDK&locale=en_US",
        ]
        for endpoint in endpoints:
            try:
                headers = {**self.get_headers(), "Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
                if page_url:
                    headers["Referer"] = page_url
                response = self.http_get(endpoint, headers=headers)
                payload = response.json()
                if isinstance(payload, dict):
                    if isinstance(payload.get("project"), dict):
                        return payload.get("project")
                    if isinstance(payload.get("projects"), list) and payload.get("projects"):
                        return payload.get("projects")[0]
            except Exception:
                continue
        return None

    def extract_modules(self, modules_list: list) -> list:
        qualities = []
        image_count = 0

        for module in modules_list or []:
            module_type = str(module.get("type") or module.get("__typename") or "").lower()

            if module_type in {"image", "media", "media_collection", "gallery"}:
                for image_item in self._collect_image_items(module):
                    image_url = self._get_largest_image_url(image_item)
                    if not image_url:
                        continue
                    image_count += 1
                    qualities.append(
                        {
                            "label": f"Image {image_count}: {self._truncate(self._clean_text(image_item.get('alt_text') or image_item.get('title') or 'Untitled'), 40)}",
                            "url": image_url,
                            "size_bytes": self._estimate_image_size(image_item),
                            "codec": None,
                            "bitrate": None,
                            "hdr": False,
                            "format": self._guess_image_format(image_url),
                        }
                    )

            elif module_type in {"video", "embed_video", "project_video"}:
                video_url = self._extract_video_url(module)
                if not video_url:
                    continue
                qualities.append(
                    {
                        "label": "Project Video",
                        "url": video_url,
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "format": "mp4" if ".mp4" in video_url.lower() else "video",
                    }
                )

            elif module_type in {"embed", "external_embed"}:
                embed_url = self._extract_embed_url(module)
                if not embed_url:
                    continue
                qualities.append(
                    {
                        "label": "External Embed Source",
                        "url": embed_url,
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "format": "url",
                        "external_platform": self._detect_external_platform(embed_url),
                        "external_url": embed_url,
                    }
                )

        if image_count >= 5:
            first_image = next((item for item in qualities if self._is_image_quality(item)), None)
            if first_image:
                qualities.insert(
                    0,
                    {
                        "label": f"All Images (ZIP) · {image_count} items",
                        "url": first_image.get("url"),
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "format": first_image.get("format") or "jpg",
                        "collection_urls": [
                            item.get("url") for item in qualities if self._is_image_quality(item) and item.get("url")
                        ],
                    },
                )

        return qualities

    def build_qualities_from_project(self, modules: list) -> list:
        return self.extract_modules(modules)

    def _extract_fallback_qualities(self, html_content: str, project_data: dict) -> list:
        image_urls = []
        video_urls = []
        preferred_images = []

        if project_data:
            cover = self._extract_thumbnail(project_data)
            if cover:
                image_urls.append(cover)
                preferred_images.append(cover)
            if project_data.get("image"):
                image_urls.append(project_data.get("image"))
                preferred_images.append(project_data.get("image"))

        for key in [
            "og:image",
            "og:image:secure_url",
            "twitter:image",
            "twitter:image:src",
            "twitter:image:secure_url",
        ]:
            value = self._extract_meta_content(html_content, key)
            if value:
                image_urls.append(value)
                preferred_images.append(value)

        image_urls.extend(self._extract_html_image_urls(html_content))
        video_urls.extend(self._extract_html_video_urls(html_content))

        image_urls = self._prioritize_image_urls(image_urls, preferred_images)
        video_urls = self._dedupe_urls(video_urls)
        if video_urls:
            video_urls = sorted(video_urls, key=self._score_media_url, reverse=True)

        qualities = []

        for index, video_url in enumerate(video_urls[:10], start=1):
            is_hls = ".m3u8" in video_url.lower()
            qualities.append(
                {
                    "label": f"Project Video {index}",
                    "url": video_url,
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": "m3u8" if is_hls else self._guess_media_format(video_url),
                    "is_hls": is_hls,
                }
            )

        for index, image_url in enumerate(image_urls[:40], start=1):
            qualities.append(
                {
                    "label": f"Image {index}",
                    "url": image_url,
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": self._guess_image_format(image_url),
                }
            )

        if len(image_urls) >= 5:
            first_image = next((item for item in qualities if self._is_image_quality(item)), None)
            if first_image:
                qualities.insert(
                    0,
                    {
                        "label": f"All Images (ZIP) · {len(image_urls)} items",
                        "url": first_image.get("url"),
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "format": first_image.get("format") or "jpg",
                        "collection_urls": image_urls[:60],
                    },
                )

        return qualities

    def _fetch_page_html(self, url: str) -> str:
        try:
            response = self.http_get(url)
            return response.text
        except Exception as exc:
            raise ExtractorError(
                "Failed to load Behance project page",
                platform=self.PLATFORM_ID,
                url=url,
            ) from exc

    def _extract_html_image_urls(self, html_content: str) -> list:
        if not html_content:
            return []
        matches = re.findall(
            r"https?://[^\s\"']+\.(?:jpg|jpeg|png|gif|webp)(?:\?[^\s\"']*)?",
            html_content,
            flags=re.IGNORECASE,
        )
        return [self._normalize_media_url(value) for value in matches if value]

    def _extract_html_video_urls(self, html_content: str) -> list:
        if not html_content:
            return []
        matches = re.findall(
            r"https?://[^\s\"']+\.(?:mp4|m3u8|webm|mov)(?:\?[^\s\"']*)?",
            html_content,
            flags=re.IGNORECASE,
        )
        return [self._normalize_media_url(value) for value in matches if value]

    def _normalize_media_url(self, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            return ""
        cleaned = cleaned.replace("\\u0026", "&").replace("\\/", "/")
        return cleaned

    def _dedupe_urls(self, urls: list) -> list:
        unique = []
        seen = set()
        for value in urls or []:
            cleaned = self._normalize_media_url(value)
            if not cleaned or not cleaned.startswith("http"):
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            unique.append(cleaned)
        return unique

    def _prioritize_image_urls(self, urls: list, preferred_images: list) -> list:
        deduped = self._dedupe_urls(urls)
        preferred = self._dedupe_urls(preferred_images)
        preferred_set = set(preferred)

        filtered = [value for value in deduped if not self._is_noise_image_url(value)]
        if not filtered:
            filtered = deduped

        project_urls = [value for value in filtered if self._is_project_asset_url(value)]
        if project_urls:
            filtered = project_urls

        def score(value: str) -> int:
            total = self._score_media_url(value)
            if value in preferred_set:
                total += 100000
            if self._is_project_asset_url(value):
                total += 20000
            return total

        return sorted(filtered, key=score, reverse=True)

    def _is_project_asset_url(self, url_value: str) -> bool:
        lowered = (url_value or "").lower()
        if "project_modules" in lowered or "project_module" in lowered:
            return True
        if "project_cover" in lowered:
            return True
        if "cdn.behance.net" in lowered and "/project" in lowered:
            return True
        return False

    def _is_noise_image_url(self, url_value: str) -> bool:
        lowered = (url_value or "").lower()
        if not lowered:
            return True
        for marker in [
            "behance.net/assets",
            "behance.net/site",
            "/site/",
            "/static/",
            "/icons/",
            "/icon/",
            "favicon",
            "sprite",
            "badge",
            "wordmark",
        ]:
            if marker in lowered:
                return True
        if "behance" in lowered and "logo" in lowered:
            return True
        if "adobe" in lowered and "logo" in lowered:
            return True
        if "avatar" in lowered and "behance" in lowered:
            return True
        return False

    def _score_media_url(self, url_value: str) -> int:
        lowered = (url_value or "").lower()
        score = 0
        if "original" in lowered or "source" in lowered or "max" in lowered:
            score += 1000
        if "project_modules" in lowered or "project_module" in lowered:
            score += 1500
        if "project_cover" in lowered:
            score += 1200
        if "cover" in lowered:
            score += 200
        match = re.search(r"(\d{3,4})", lowered)
        if match:
            score += int(match.group(1))
        return score

    def _guess_media_format(self, url_value: str) -> str:
        lowered = (url_value or "").lower()
        if ".webm" in lowered:
            return "webm"
        if ".mov" in lowered:
            return "mov"
        if ".mp4" in lowered:
            return "mp4"
        if ".m3u8" in lowered:
            return "m3u8"
        return "video"

    def _extract_project_data(self, html_content: str, project_id: str):
        payloads = []

        next_data_block = re.search(
            r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
            html_content,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if next_data_block:
            payload = self._safe_json_loads(next_data_block.group(1))
            if payload:
                payloads.append(payload)

        for pattern in [
            r"window\.__NEXT_DATA__\s*=\s*(\{.*?\})\s*;",
            r"window\.__data__\s*=\s*(\{.*?\})\s*;",
        ]:
            match = re.search(pattern, html_content, flags=re.IGNORECASE | re.DOTALL)
            if match:
                payload = self._safe_json_loads(match.group(1))
                if payload:
                    payloads.append(payload)

        for payload in payloads:
            project = self._find_project_node(payload, project_id)
            if project:
                return project

        return None

    def _extract_project_id(self, url: str) -> str:
        match = re.search(r"/gallery/(\d+)", url)
        if match:
            return match.group(1)

        parsed = urlparse(url)
        path = parsed.path or ""
        fallback = re.search(r"/(\d+)(?:/|$)", path)
        if fallback:
            return fallback.group(1)
        return ""

    def _extract_author(self, project_data: dict) -> str:
        owners = project_data.get("owners")
        if isinstance(owners, list) and owners:
            first = owners[0] or {}
            return self._clean_text(first.get("display_name") or first.get("username") or "")
        owner = project_data.get("owner")
        if isinstance(owner, dict):
            return self._clean_text(owner.get("display_name") or owner.get("username") or "")
        return ""

    def _extract_thumbnail(self, project_data: dict) -> str:
        covers = project_data.get("covers")
        if isinstance(covers, dict):
            for key in ["original", "max_404", "404", "202"]:
                if covers.get(key):
                    return covers.get(key)
            values = [value for value in covers.values() if isinstance(value, str)]
            if values:
                return values[0]

        if project_data.get("image"):
            return project_data.get("image")

        return ""

    def _find_project_node(self, payload, project_id: str):
        target_id = str(project_id) if project_id else ""

        def walk(node):
            if isinstance(node, dict):
                if self._looks_like_project_dict(node, target_id):
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

    def _looks_like_project_dict(self, node: dict, target_id: str) -> bool:
        if not isinstance(node, dict):
            return False
        has_modules = isinstance(node.get("modules"), list)
        has_title = bool(node.get("name") or node.get("title"))
        node_id = str(node.get("id") or "")
        if target_id and node_id and target_id == node_id and has_modules:
            return True
        if has_modules and has_title and ("owners" in node or "owner" in node):
            return True
        return False

    def _collect_image_items(self, module: dict) -> list:
        image_items = []

        direct_candidates = [
            module,
            module.get("image") if isinstance(module.get("image"), dict) else None,
            module.get("src") if isinstance(module.get("src"), dict) else None,
        ]
        for candidate in direct_candidates:
            if isinstance(candidate, dict):
                image_items.append(candidate)

        for key in ["images", "media", "items", "components"]:
            values = module.get(key)
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, dict):
                        image_items.append(value)

        unique = []
        seen = set()
        for item in image_items:
            marker = json.dumps(item, sort_keys=True, default=str)
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(item)
        return unique

    def _get_largest_image_url(self, image_item: dict) -> str:
        candidate_urls = []

        for key in ["src_original", "src_max", "src", "url", "original", "source"]:
            value = image_item.get(key)
            if isinstance(value, str) and value.startswith("http"):
                candidate_urls.append(value)

        sizes = image_item.get("sizes")
        if isinstance(sizes, dict):
            for key in ["source", "max_3840", "max_2800", "max_1920", "max_1240", "disp"]:
                value = sizes.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    candidate_urls.append(value)
                elif isinstance(value, dict):
                    src = value.get("src") or value.get("url")
                    if isinstance(src, str) and src.startswith("http"):
                        candidate_urls.append(src)

        for key in ["full", "display_url", "photo"]:
            value = image_item.get(key)
            if isinstance(value, str) and value.startswith("http"):
                candidate_urls.append(value)

        if not candidate_urls:
            return ""

        def score(url_value: str) -> int:
            lowered = url_value.lower()
            total = 0
            if "source" in lowered or "original" in lowered:
                total += 100
            size_match = re.search(r"(\d{3,4})", lowered)
            if size_match:
                total += int(size_match.group(1))
            return total

        candidate_urls = sorted(set(candidate_urls), key=score, reverse=True)
        return candidate_urls[0]

    def _extract_video_url(self, module: dict) -> str:
        for key in ["source", "url", "src", "video_url", "mp4_url"]:
            value = module.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
            if isinstance(value, dict):
                nested = value.get("url") or value.get("src")
                if isinstance(nested, str) and nested.startswith("http"):
                    return nested

        for key in ["video", "player", "embed"]:
            value = module.get(key)
            if isinstance(value, dict):
                nested_url = value.get("url") or value.get("src") or value.get("embed_url")
                if isinstance(nested_url, str) and nested_url.startswith("http"):
                    return nested_url

        serialized = json.dumps(module, default=str)
        match = re.search(r"https?://[^\s\"']+\.(?:mp4|m3u8)[^\s\"']*", serialized, flags=re.IGNORECASE)
        if match:
            return match.group(0)

        return ""

    def _extract_embed_url(self, module: dict) -> str:
        for key in ["src", "url", "embed_url", "iframe", "link"]:
            value = module.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
            if isinstance(value, dict):
                nested = value.get("url") or value.get("src")
                if isinstance(nested, str) and nested.startswith("http"):
                    return nested
        serialized = json.dumps(module, default=str)
        match = re.search(r"https?://(?:www\.)?(youtube\.com|youtu\.be|vimeo\.com)/[^\s\"']+", serialized, flags=re.IGNORECASE)
        if match:
            return match.group(0)
        return ""

    def _detect_external_platform(self, embed_url: str) -> str:
        lowered = (embed_url or "").lower()
        if "youtube" in lowered or "youtu.be" in lowered:
            return "youtube"
        if "vimeo" in lowered:
            return "vimeo"
        if "soundcloud" in lowered:
            return "soundcloud"
        return "external"

    def _estimate_image_size(self, image_item: dict):
        width = self._to_int(image_item.get("width"))
        height = self._to_int(image_item.get("height"))
        if not width or not height:
            return None
        estimated = int(width * height * 0.22)
        return estimated if estimated > 0 else None

    def _guess_image_format(self, image_url: str) -> str:
        lowered = (image_url or "").lower()
        for ext in ["png", "webp", "gif", "jpeg", "jpg"]:
            if f".{ext}" in lowered:
                if ext == "jpeg":
                    return "jpg"
                return ext
        return "jpg"

    def _is_image_quality(self, quality: dict) -> bool:
        fmt = str(quality.get("format") or "").lower()
        return fmt in {"jpg", "jpeg", "png", "gif", "webp"}

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

    def _to_int(self, value):
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
