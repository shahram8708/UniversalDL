import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import httpx

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class PodcastRSSExtractor(BaseExtractor):
    PLATFORM_ID = "podcast_rss"
    REQUIRES_HEADLESS = False
    REQUIRES_PROXY = False
    TEST_URL = "https://feeds.simplecast.com/54nAGcIl"

    XML_NAMESPACES = {
        "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
        "atom": "http://www.w3.org/2005/Atom",
        "media": "http://search.yahoo.com/mrss/",
    }

    def extract(self, url: str) -> dict:
        try:
            if self.detect_if_rss_feed(url):
                return self.extract_from_rss_feed(url)
            return self.extract_from_episode_page(url)
        except ExtractorError:
            raise
        except httpx.HTTPError as exc:
            raise ExtractorError(
                "Network error while loading podcast source",
                platform=self.PLATFORM_ID,
                url=url,
            ) from exc
        except ET.ParseError as exc:
            raise ExtractorError(
                "Failed to parse RSS or Atom feed XML",
                platform=self.PLATFORM_ID,
                url=url,
            ) from exc
        except Exception as exc:
            raise ExtractorError(
                "Failed to extract podcast media",
                platform=self.PLATFORM_ID,
                url=url,
            ) from exc

    def detect_if_rss_feed(self, url: str) -> bool:
        if self._looks_like_feed_url(url):
            return True

        try:
            with self.create_http_client(
                headers=self.get_headers(),
                timeout=15,
                follow_redirects=True,
            ) as client:
                response = client.head(url)
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if self._is_feed_content_type(content_type):
                    return True
        except httpx.HTTPError:
            pass

        try:
            with self.create_http_client(
                headers=self.get_headers(),
                timeout=15,
                follow_redirects=True,
            ) as client:
                response = client.get(url, headers={"Range": "bytes=0-2048", **self.get_headers()})
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if self._is_feed_content_type(content_type):
                    return True
        except httpx.HTTPError:
            return False

        return False

    def extract_from_rss_feed(self, feed_url: str) -> dict:
        response = self.http_get(feed_url)

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise ExtractorError(
                "The URL does not contain valid RSS or Atom XML content",
                platform=self.PLATFORM_ID,
                url=feed_url,
            ) from exc

        root_name = self._local_name(root.tag).lower()
        if root_name == "rss":
            return self._extract_rss_feed(root, feed_url)
        if root_name == "feed":
            return self._extract_atom_feed(root, feed_url)

        raise ExtractorError(
            "Unsupported feed format. Expected RSS 2.0 or Atom 1.0",
            platform=self.PLATFORM_ID,
            url=feed_url,
        )

    def extract_from_episode_page(self, url: str) -> dict:
        response = self.http_get(url)
        html_content = response.text

        title = self._extract_meta_content(html_content, "og:title") or self._extract_title_tag(html_content) or "Podcast Episode"
        description = self._extract_meta_content(html_content, "og:description")
        thumbnail = self._extract_meta_content(html_content, "og:image")
        author = self._extract_meta_content(html_content, "author") or self._extract_meta_content(html_content, "article:author")

        audio_candidates = []
        audio_candidates.extend(self._extract_audio_tag_sources(html_content, url))
        audio_candidates.extend(self._extract_meta_audio_sources(html_content, url))
        audio_candidates.extend(self._extract_json_ld_audio_sources(html_content, url))
        audio_candidates.extend(self._extract_data_attribute_audio_sources(html_content, url))
        audio_candidates.extend(self._extract_platform_specific_audio_sources(html_content, url))

        audio_url = self._first_valid_audio_url(audio_candidates)
        if not audio_url:
            feed_url = self._extract_feed_link_from_html(html_content, url)
            if feed_url and feed_url != url:
                return self.extract_from_rss_feed(feed_url)
            raise ExtractorError(
                "No podcast audio stream was found on this episode page",
                platform=self.PLATFORM_ID,
                url=url,
            )

        file_type = self._guess_file_type(audio_url)
        codec, output_format = self._codec_and_format(file_type, audio_url)

        duration = self._extract_duration_from_html(html_content)

        return {
            "title": title,
            "author": author,
            "channel_id": url,
            "thumbnail": thumbnail,
            "duration": duration,
            "view_count": None,
            "description": self._truncate(description, 500),
            "upload_date": None,
            "qualities": [
                {
                    "label": "Episode Audio",
                    "url": audio_url,
                    "size_bytes": None,
                    "codec": codec,
                    "bitrate": None,
                    "hdr": False,
                    "format": output_format,
                }
            ],
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {},
        }

    def _extract_rss_feed(self, root: ET.Element, feed_url: str) -> dict:
        channel = root.find("channel")
        if channel is None:
            raise ExtractorError("RSS feed is missing channel metadata", platform=self.PLATFORM_ID, url=feed_url)

        channel_title = self._clean_text(channel.findtext("title")) or "Podcast"
        channel_author = self._clean_text(channel.findtext("author"))
        if not channel_author:
            channel_author = self._clean_text(channel.findtext("itunes:author", namespaces=self.XML_NAMESPACES))
        channel_description = self._truncate(self._clean_text(channel.findtext("description")), 500)

        channel_image = None
        itunes_image = channel.find("itunes:image", namespaces=self.XML_NAMESPACES)
        if itunes_image is not None:
            channel_image = itunes_image.attrib.get("href")
        if not channel_image:
            channel_image = self._clean_text(channel.findtext("image/url"))

        episodes = []
        for item in channel.findall("item"):
            enclosure = item.find("enclosure")
            if enclosure is None:
                continue

            episode_url = self._normalize_candidate_url(enclosure.attrib.get("url"), feed_url)
            if not episode_url:
                continue

            episode_title = self._clean_text(item.findtext("title")) or "Untitled Episode"
            episode_description = self._clean_text(item.findtext("description"))
            episode_duration = self._parse_duration_to_seconds(
                self._clean_text(item.findtext("itunes:duration", namespaces=self.XML_NAMESPACES))
            )
            episode_pubdate = self._clean_text(item.findtext("pubDate"))
            file_type = str(enclosure.attrib.get("type") or self._guess_file_type(episode_url)).lower()
            file_length = enclosure.attrib.get("length")

            episodes.append(
                {
                    "title": episode_title,
                    "description": episode_description,
                    "url": episode_url,
                    "duration": episode_duration,
                    "pubdate": episode_pubdate,
                    "file_type": file_type,
                    "file_length": file_length,
                }
            )

        if not episodes:
            raise ExtractorError("This RSS feed has no episodes", platform=self.PLATFORM_ID, url=feed_url)

        latest = episodes[0]
        qualities = self._build_episode_qualities(episodes)

        return {
            "title": f"{channel_title} — {latest.get('title')}",
            "author": channel_author,
            "channel_id": feed_url,
            "thumbnail": channel_image,
            "duration": latest.get("duration"),
            "view_count": None,
            "description": channel_description,
            "upload_date": self._format_pubdate(latest.get("pubdate")),
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {},
        }

    def _extract_atom_feed(self, root: ET.Element, feed_url: str) -> dict:
        feed_title = self._find_child_text(root, {"title"}) or "Podcast"
        feed_author = self._extract_atom_author(root)
        feed_description = self._truncate(self._find_child_text(root, {"subtitle", "summary"}), 500)
        feed_thumbnail = self._extract_atom_thumbnail(root)

        entries = [node for node in root.iter() if self._local_name(node.tag).lower() == "entry"]
        episodes = []
        for entry in entries:
            entry_title = self._find_child_text(entry, {"title"}) or "Untitled Episode"
            entry_pubdate = self._find_child_text(entry, {"published", "updated"})
            entry_duration = self._parse_duration_to_seconds(
                self._find_child_text(entry, {"duration"})
                or self._clean_text(entry.findtext("itunes:duration", namespaces=self.XML_NAMESPACES))
            )

            enclosure_url = None
            enclosure_type = None
            enclosure_length = None
            for link in entry.findall("{*}link"):
                rel = str(link.attrib.get("rel") or "").lower()
                href = self._normalize_candidate_url(link.attrib.get("href"), feed_url)
                link_type = str(link.attrib.get("type") or "").lower()
                if not href:
                    continue
                if rel == "enclosure":
                    enclosure_url = href
                    enclosure_type = link_type
                    enclosure_length = link.attrib.get("length")
                    break
                if not enclosure_url and rel in {"alternate", ""} and (
                    "audio" in link_type or self._looks_like_audio_url(href)
                ):
                    enclosure_url = href
                    enclosure_type = link_type
                    enclosure_length = link.attrib.get("length")

            if not enclosure_url:
                continue

            episodes.append(
                {
                    "title": entry_title,
                    "description": self._find_child_text(entry, {"summary", "content"}),
                    "url": enclosure_url,
                    "duration": entry_duration,
                    "pubdate": entry_pubdate,
                    "file_type": enclosure_type or self._guess_file_type(enclosure_url),
                    "file_length": enclosure_length,
                }
            )

        if not episodes:
            raise ExtractorError("This RSS feed has no episodes", platform=self.PLATFORM_ID, url=feed_url)

        latest = episodes[0]
        qualities = self._build_episode_qualities(episodes)

        return {
            "title": f"{feed_title} — {latest.get('title')}",
            "author": feed_author,
            "channel_id": feed_url,
            "thumbnail": feed_thumbnail,
            "duration": latest.get("duration"),
            "view_count": None,
            "description": feed_description,
            "upload_date": self._format_pubdate(latest.get("pubdate")),
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": False,
            "manifest_url": None,
            "headers_required": {},
        }

    def _build_episode_qualities(self, episodes: list) -> list:
        qualities = []
        for index, episode in enumerate(episodes[:10]):
            audio_url = episode.get("url")
            if not audio_url:
                continue

            file_type = str(episode.get("file_type") or "").lower()
            codec, output_format = self._codec_and_format(file_type, audio_url)
            size_bytes = self._to_int(episode.get("file_length"))
            episode_title = episode.get("title") or f"Episode {index + 1}"

            qualities.append(
                {
                    "label": f"Episode: {episode_title[:60]}",
                    "url": audio_url,
                    "size_bytes": size_bytes,
                    "codec": codec,
                    "bitrate": None,
                    "hdr": False,
                    "format": output_format,
                    "episode_number": index + 1,
                    "episode_title": episode_title,
                    "pubdate": episode.get("pubdate"),
                }
            )
        return qualities

    def _extract_audio_tag_sources(self, html_content: str, base_url: str) -> list:
        candidates = []
        patterns = [
            r"<audio[^>]+src=[\"']([^\"']+)[\"']",
            r"<source[^>]+src=[\"']([^\"']+)[\"']",
        ]
        for pattern in patterns:
            for match in re.findall(pattern, html_content, flags=re.IGNORECASE):
                candidates.append(self._normalize_candidate_url(match, base_url))
        return candidates

    def _extract_meta_audio_sources(self, html_content: str, base_url: str) -> list:
        keys = ["og:audio", "og:audio:secure_url", "twitter:player:stream"]
        candidates = []
        for key in keys:
            value = self._extract_meta_content(html_content, key)
            if value:
                candidates.append(self._normalize_candidate_url(value, base_url))
        return candidates

    def _extract_json_ld_audio_sources(self, html_content: str, base_url: str) -> list:
        candidates = []
        blocks = re.findall(
            r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
            html_content,
            flags=re.IGNORECASE | re.DOTALL,
        )
        for block in blocks:
            payload = self._load_json_payload(block)
            for content_url in self._collect_content_urls(payload):
                candidates.append(self._normalize_candidate_url(content_url, base_url))
        return candidates

    def _extract_data_attribute_audio_sources(self, html_content: str, base_url: str) -> list:
        candidates = []
        for pattern in (r"data-url=[\"']([^\"']+)[\"']", r"data-src=[\"']([^\"']+)[\"']"):
            for match in re.findall(pattern, html_content, flags=re.IGNORECASE):
                candidates.append(self._normalize_candidate_url(match, base_url))
        return candidates

    def _extract_platform_specific_audio_sources(self, html_content: str, base_url: str) -> list:
        candidates = []
        patterns = [
            r'"audioUrl"\s*:\s*"([^\"]+)"',
            r'"audio_url"\s*:\s*"([^\"]+)"',
            r'"enclosureUrl"\s*:\s*"([^\"]+)"',
            r"https?://[^\s\"']+\.(?:mp3|m4a|aac|ogg|wav)(?:\?[^\s\"']*)?",
            r"https?://[^\s\"']+\.simplecastaudio\.com/[^\s\"']+",
            r"https?://traffic\.libsyn\.com/[^\s\"']+",
            r"https?://cdn\.transistor\.fm/[^\s\"']+",
        ]
        for pattern in patterns:
            for match in re.findall(pattern, html_content, flags=re.IGNORECASE):
                candidates.append(self._normalize_candidate_url(match, base_url))
        return candidates

    def _extract_feed_link_from_html(self, html_content: str, base_url: str) -> str:
        candidates = []
        patterns = [
            r"<link[^>]+type=[\"']application/rss\+xml[\"'][^>]+href=[\"']([^\"']+)[\"']",
            r"<link[^>]+type=[\"']application/atom\+xml[\"'][^>]+href=[\"']([^\"']+)[\"']",
            r"<link[^>]+rel=[\"']alternate[\"'][^>]+type=[\"']application/(?:rss\+xml|atom\+xml|xml)[\"'][^>]+href=[\"']([^\"']+)[\"']",
        ]
        for pattern in patterns:
            for match in re.findall(pattern, html_content, flags=re.IGNORECASE):
                candidates.append(match)

        json_patterns = [
            r"\"rssUrl\"\s*:\s*\"([^\"]+)\"",
            r"\"feedUrl\"\s*:\s*\"([^\"]+)\"",
            r"\"feed_url\"\s*:\s*\"([^\"]+)\"",
        ]
        for pattern in json_patterns:
            for match in re.findall(pattern, html_content, flags=re.IGNORECASE):
                candidates.append(match)

        for match in re.findall(
            r"https?://[^\s\"']+(?:rss|feed|podcast\.xml|rss\.xml|feed\.xml)[^\s\"']*",
            html_content,
            flags=re.IGNORECASE,
        ):
            candidates.append(match)

        seen = set()
        for candidate in candidates:
            normalized = self._normalize_candidate_url(candidate, base_url)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            if self._looks_like_feed_url(normalized):
                return normalized
        return ""

    def _extract_duration_from_html(self, html_content: str):
        duration_candidates = []

        meta_duration = self._extract_meta_content(html_content, "duration")
        if meta_duration:
            duration_candidates.append(meta_duration)

        for match in re.findall(r'"duration"\s*:\s*"([^\"]+)"', html_content, flags=re.IGNORECASE):
            duration_candidates.append(match)

        for candidate in duration_candidates:
            parsed = self._parse_duration_to_seconds(candidate)
            if parsed:
                return parsed
        return None

    def _extract_title_tag(self, html_content: str) -> str:
        match = re.search(r"<title>(.*?)</title>", html_content, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return self._clean_text(match.group(1))

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

    def _find_child_text(self, parent: ET.Element, names: set) -> str:
        for child in list(parent):
            if self._local_name(child.tag).lower() in {item.lower() for item in names}:
                return self._clean_text(child.text)
        return ""

    def _extract_atom_author(self, root: ET.Element) -> str:
        for child in root:
            if self._local_name(child.tag).lower() != "author":
                continue
            name = self._find_child_text(child, {"name"})
            if name:
                return name
        return ""

    def _extract_atom_thumbnail(self, root: ET.Element) -> str:
        for child in root:
            local_name = self._local_name(child.tag).lower()
            if local_name in {"logo", "icon"} and self._clean_text(child.text):
                return self._clean_text(child.text)
            if local_name == "link":
                rel = str(child.attrib.get("rel") or "").lower()
                if rel == "image" and child.attrib.get("href"):
                    return self._clean_text(child.attrib.get("href"))
        image_node = root.find("itunes:image", namespaces=self.XML_NAMESPACES)
        if image_node is not None and image_node.attrib.get("href"):
            return self._clean_text(image_node.attrib.get("href"))
        return ""

    def _is_feed_content_type(self, value: str) -> bool:
        lowered = str(value or "").lower()
        markers = ["rss", "xml", "atom", "feed"]
        return any(marker in lowered for marker in markers)

    def _looks_like_feed_url(self, url: str) -> bool:
        parsed = urlparse(url)
        path = (parsed.path or "").lower()
        target = f"{parsed.netloc}{path}".lower()
        if path.endswith(".xml"):
            return True
        if re.search(r"(/|^)(feed|rss)(/|\.|$)", path):
            return True
        if path.endswith(".rss"):
            return True
        if any(marker in target for marker in ["/feed", "/rss", "/podcast.xml", "feeds."]):
            return True
        return False

    def _first_valid_audio_url(self, candidates: list) -> str:
        seen = set()
        for candidate in candidates:
            if not candidate:
                continue
            normalized = candidate.strip()
            if normalized in seen:
                continue
            seen.add(normalized)
            if self._looks_like_audio_url(normalized) or ".m3u8" in normalized.lower():
                return normalized
        return ""

    def _normalize_candidate_url(self, value: str, base_url: str) -> str:
        if not value:
            return ""
        cleaned = html.unescape(str(value).strip())
        cleaned = cleaned.replace("\\/", "/")
        if cleaned.startswith("//"):
            parsed = urlparse(base_url)
            cleaned = f"{parsed.scheme}:{cleaned}"
        return urljoin(base_url, cleaned)

    def _looks_like_audio_url(self, value: str) -> bool:
        lowered = str(value or "").lower()
        return bool(re.search(r"\.(mp3|m4a|aac|wav|ogg|opus)(\?|$)", lowered))

    def _guess_file_type(self, url: str) -> str:
        value = str(url or "").lower()
        if ".mp3" in value:
            return "audio/mpeg"
        if ".m4a" in value or ".mp4" in value or ".aac" in value:
            return "audio/mp4"
        if ".ogg" in value:
            return "audio/ogg"
        if ".wav" in value:
            return "audio/wav"
        return "audio/mpeg"

    def _codec_and_format(self, file_type: str, url: str):
        lowered = str(file_type or "").lower()
        url_value = str(url or "").lower()
        if "mpeg" in lowered or ".mp3" in url_value:
            return "mp3", "mp3"
        if "aac" in lowered or "mp4" in lowered or ".m4a" in url_value or ".aac" in url_value:
            return "aac", "m4a"
        if "ogg" in lowered or ".ogg" in url_value:
            return "ogg", "ogg"
        if "wav" in lowered or ".wav" in url_value:
            return "wav", "wav"
        return "mp3", "mp3"

    def _parse_duration_to_seconds(self, value: str):
        text = self._clean_text(value)
        if not text:
            return None

        if text.isdigit():
            try:
                return int(text)
            except ValueError:
                return None

        iso_match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", text, flags=re.IGNORECASE)
        if iso_match:
            hours = int(iso_match.group(1) or 0)
            minutes = int(iso_match.group(2) or 0)
            seconds = int(iso_match.group(3) or 0)
            return hours * 3600 + minutes * 60 + seconds

        if ":" in text:
            parts = text.split(":")
            try:
                parts_int = [int(part) for part in parts]
            except ValueError:
                return None
            if len(parts_int) == 3:
                return parts_int[0] * 3600 + parts_int[1] * 60 + parts_int[2]
            if len(parts_int) == 2:
                return parts_int[0] * 60 + parts_int[1]

        return None

    def _format_pubdate(self, value: str) -> str:
        text = self._clean_text(value)
        if not text:
            return None
        try:
            parsed = parsedate_to_datetime(text)
            return parsed.strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            pass

        try:
            iso = text.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(iso)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            return None

    def _to_int(self, value):
        if value is None:
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _load_json_payload(self, raw: str):
        data = self._clean_text(raw)
        if not data:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None

    def _collect_content_urls(self, payload):
        results = []

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    lowered_key = str(key).lower()
                    if lowered_key in {"contenturl", "audio", "audio_url", "url"} and isinstance(value, str):
                        results.append(value)
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        return results

    def _truncate(self, value: str, limit: int) -> str:
        text = self._clean_text(value)
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    def _clean_text(self, value) -> str:
        if value is None:
            return ""
        text = html.unescape(str(value))
        return re.sub(r"\s+", " ", text).strip()

    def _local_name(self, tag: str) -> str:
        if not tag:
            return ""
        if "}" in tag:
            return tag.split("}", 1)[1]
        return tag
