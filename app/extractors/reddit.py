import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import xml.etree.ElementTree as ET

import httpx

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class RedditExtractor(BaseExtractor):
    PLATFORM_ID = "reddit"
    REQUIRES_HEADLESS = False
    REQUIRES_PROXY = False
    TEST_URL = "https://www.reddit.com/r/videos/comments/12345/example/"

    def extract(self, url: str) -> dict:
        headers = self._build_request_headers()
        resolved_url = self._resolve_post_url(url, headers=headers)
        candidate_json_urls = self._build_json_urls(resolved_url)
        if not candidate_json_urls:
            raise ExtractorError("Unsupported Reddit URL", platform=self.PLATFORM_ID, url=url)

        data = self._fetch_post_json(candidate_json_urls, headers=headers, original_url=url)
        post = self._extract_primary_post(data, original_url=url)
        source_post = self._resolve_media_source_post(post)

        qualities = []
        content_type = "post"
        is_hls = False
        manifest_url = None

        if source_post.get("is_video"):
            content_type = "video"
            reddit_video = (
                (source_post.get("media") or {}).get("reddit_video")
                or (source_post.get("secure_media") or {}).get("reddit_video")
                or {}
            )
            dash_url = reddit_video.get("dash_url")
            if dash_url:
                qualities, audio_url = self._parse_dash(dash_url)
                for entry in qualities:
                    entry["audio_url"] = audio_url
            fallback_url = reddit_video.get("fallback_url")
            if fallback_url and not qualities:
                normalized_fallback_url = fallback_url.replace("&amp;", "&")
                qualities.append(self._video_quality("original", normalized_fallback_url))
                audio_url = self._derive_audio_url(normalized_fallback_url)
                if audio_url:
                    qualities[0]["audio_url"] = audio_url
            hls_url = reddit_video.get("hls_url")
            if hls_url:
                is_hls = True
                manifest_url = hls_url
        elif source_post.get("is_gallery"):
            content_type = "image"
            media_metadata = source_post.get("media_metadata") or {}
            for index, item in enumerate(media_metadata.values(), start=1):
                if "s" not in item:
                    continue
                url_value = item["s"].get("u")
                if url_value:
                    mime_value = str(item.get("m") or "").strip().lower()
                    format_value = mime_value.split("/")[-1] if "/" in mime_value else "jpg"
                    if format_value not in {"jpg", "jpeg", "png"}:
                        format_value = "jpg"
                    qualities.append(
                        {
                            "label": f"image_{index}",
                            "url": url_value.replace("&amp;", "&"),
                            "size_bytes": None,
                            "codec": None,
                            "bitrate": None,
                            "hdr": False,
                            "format": format_value,
                        }
                    )
        else:
            link_url = source_post.get("url") or ""
            link_path = (urlparse(link_url).path or "").strip().lower()
            if any(link_path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".gifv", ".webp")):
                content_type = "image"
                qualities.append(
                    {
                        "label": "image",
                        "url": link_url,
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "format": os.path.splitext(link_url)[1].lstrip(".") or "jpg",
                    }
                )
            elif link_url:
                content_type = "video"
                qualities.append(self._video_quality("external", link_url))

        upload_date = None
        created = post.get("created_utc")
        if created:
            upload_date = datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%d")

        return {
            "title": post.get("title"),
            "author": post.get("author"),
            "channel_id": None,
            "thumbnail": post.get("thumbnail") if post.get("thumbnail", "").startswith("http") else None,
            "duration": None,
            "view_count": post.get("score"),
            "description": (post.get("selftext") or "")[:500] if post.get("selftext") else None,
            "upload_date": upload_date,
            "content_type": content_type,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": is_hls,
            "manifest_url": manifest_url,
            "headers_required": {},
        }

    def _build_request_headers(self) -> dict:
        headers = self.get_headers()
        headers.update(
            {
                "Accept": "application/json,text/plain,*/*",
                "Origin": "https://www.reddit.com",
                "Referer": "https://www.reddit.com/",
            }
        )
        return headers

    def _resolve_post_url(self, url: str, headers: dict) -> str:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        path = (parsed.path or "").lower()

        if hostname not in {"redd.it", "www.redd.it"} and "/s/" not in path:
            return url

        try:
            with self.create_http_client(
                timeout=20,
                follow_redirects=True,
                headers=headers,
            ) as client:
                response = client.get(url)
                return str(response.url)
        except httpx.HTTPError:
            return url

    def _build_json_urls(self, url: str) -> list:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        normalized_path = self._normalize_comments_path(hostname, parsed.path or "")
        if not normalized_path:
            return []

        json_path = f"{normalized_path}.json"
        query_items = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=False) if key.lower() != "raw_json"]
        query_items.append(("raw_json", "1"))
        query = urlencode(query_items, doseq=True)

        hosts = []
        for candidate_host in (hostname, "www.reddit.com", "reddit.com"):
            if not candidate_host:
                continue
            canonical_host = candidate_host
            if canonical_host.startswith("old.") or canonical_host in {"redd.it", "www.redd.it"}:
                canonical_host = "www.reddit.com"
            if canonical_host not in hosts:
                hosts.append(canonical_host)

        return [urlunparse(("https", host, json_path, "", query, "")) for host in hosts]

    def _normalize_comments_path(self, hostname: str, path: str) -> str:
        value = re.sub(r"/+", "/", (path or "").strip())
        if not value:
            return ""
        if not value.startswith("/"):
            value = f"/{value}"
        if value.endswith(".json"):
            value = value[:-5]
        value = value.rstrip("/")

        if hostname in {"redd.it", "www.redd.it"}:
            post_id = value.strip("/").split("/", 1)[0].strip()
            if not post_id:
                return ""
            return f"/comments/{post_id}/"

        match = re.search(r"^(/r/[^/]+/comments/[a-z0-9]+(?:/[^/]+)?)", value, re.IGNORECASE)
        if match:
            return f"{match.group(1).rstrip('/')}/"

        match = re.search(r"^(/comments/[a-z0-9]+(?:/[^/]+)?)", value, re.IGNORECASE)
        if match:
            return f"{match.group(1).rstrip('/')}/"

        return ""

    def _fetch_post_json(self, json_urls: list, headers: dict, original_url: str):
        with self.create_http_client(
            timeout=30,
            follow_redirects=True,
            headers=headers,
        ) as client:
            for json_url in json_urls:
                try:
                    response = client.get(json_url)
                except httpx.HTTPError:
                    continue

                if response.status_code >= 400:
                    continue

                try:
                    payload = response.json()
                except ValueError:
                    continue

                if payload:
                    return payload

        raise ExtractorError("Failed to fetch Reddit post", platform=self.PLATFORM_ID, url=original_url)

    def _extract_primary_post(self, payload, original_url: str) -> dict:
        try:
            listing = payload[0] if isinstance(payload, list) else payload
            children = ((listing or {}).get("data") or {}).get("children") or []
            for child in children:
                data = (child or {}).get("data")
                if data:
                    return data
        except (AttributeError, IndexError, KeyError, TypeError):
            pass

        raise ExtractorError("Invalid Reddit response", platform=self.PLATFORM_ID, url=original_url)

    def _resolve_media_source_post(self, post: dict) -> dict:
        crossposts = post.get("crosspost_parent_list") or []
        if crossposts and isinstance(crossposts[0], dict):
            return crossposts[0]
        return post

    def _video_quality(self, label, url):
        return {
            "label": label,
            "url": url,
            "size_bytes": None,
            "codec": None,
            "bitrate": None,
            "hdr": False,
            "format": "mp4",
        }

    def _derive_audio_url(self, fallback_url):
        if not fallback_url:
            return None
        parsed = urlparse(fallback_url)
        file_name = parsed.path.rsplit("/", 1)[-1]
        if "DASH_" in file_name.upper():
            base_path = parsed.path.rsplit("/", 1)[0]
            candidates = ["DASH_audio.mp4", "DASH_AUDIO_128.mp4"]
            for candidate in candidates:
                if candidate.lower() == file_name.lower():
                    continue
                audio_path = f"{base_path}/{candidate}"
                return urlunparse((parsed.scheme, parsed.netloc, audio_path, "", parsed.query, ""))
        return None

    def _parse_dash(self, dash_url):
        try:
            response = self.http_get(dash_url)
            xml_root = ET.fromstring(response.text)
        except Exception:
            return [], None

        base_url = dash_url.rsplit("/", 1)[0]
        video_tracks = []
        audio_candidates = []

        for adaptation in self._iter_elements_by_local_name(xml_root, "AdaptationSet"):
            mime_type = str(adaptation.attrib.get("mimeType") or "").lower()
            content_type = str(adaptation.attrib.get("contentType") or "").lower()
            adaptation_base = self._extract_baseurl_text(adaptation)
            is_audio_set = "audio" in mime_type or content_type == "audio"
            is_video_set = "video" in mime_type or content_type == "video"

            for rep in self._iter_elements_by_local_name(adaptation, "Representation"):
                height = self._safe_int(rep.attrib.get("height"))
                bitrate = self._safe_int(rep.attrib.get("bandwidth"))
                rep_base = self._extract_baseurl_text(rep) or adaptation_base
                track_url = self._build_dash_asset_url(base_url, rep_base)
                if not track_url:
                    continue

                if is_audio_set:
                    audio_candidates.append((bitrate, track_url))
                    continue

                if not is_video_set and not height:
                    continue

                label = f"{height}p" if height else "video"
                track = self._video_quality(label, track_url)
                track["bitrate"] = bitrate
                track["has_audio"] = False
                video_tracks.append(track)

        unique_tracks = []
        seen_urls = set()
        for track in video_tracks:
            track_url = track.get("url")
            if not track_url or track_url in seen_urls:
                continue
            seen_urls.add(track_url)
            unique_tracks.append(track)
        unique_tracks.sort(key=self._quality_sort_key, reverse=True)

        audio_url = None
        if audio_candidates:
            audio_candidates.sort(key=lambda item: item[0] or 0, reverse=True)
            audio_url = audio_candidates[0][1]

        if not audio_url:
            audio_url = f"{base_url}/DASH_audio.mp4"

        return unique_tracks, audio_url

    def _iter_elements_by_local_name(self, element, local_name):
        for child in element.iter():
            if self._tag_local_name(child.tag) == local_name.lower():
                yield child

    def _tag_local_name(self, tag):
        return str(tag).split("}", 1)[-1].lower()

    def _extract_baseurl_text(self, element):
        for base_tag in self._iter_elements_by_local_name(element, "BaseURL"):
            value = str(base_tag.text or "").strip()
            if value:
                return value
        return None

    def _safe_int(self, value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _quality_sort_key(self, quality):
        label = str((quality or {}).get("label") or "").lower()
        match = re.search(r"(\d+)p", label)
        height = int(match.group(1)) if match else 0
        bitrate = self._safe_int((quality or {}).get("bitrate"))
        return height, bitrate

    def _build_dash_asset_url(self, base_url, asset_path):
        value = str(asset_path or "").strip()
        if not value:
            return None
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return f"{base_url}/{value.lstrip('/')}"
