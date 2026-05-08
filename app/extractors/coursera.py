import json
import logging
import re
from urllib.parse import urljoin

import m3u8
from playwright.sync_api import sync_playwright

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class CourseraExtractor(BaseExtractor):
    PLATFORM_ID = "coursera"
    REQUIRES_HEADLESS = True
    REQUIRES_PROXY = False
    TEST_URL = "https://www.coursera.org/lecture/python/welcome-to-the-course-preview"

    ENROLLED_ERROR_MESSAGE = (
        "This Coursera lecture requires enrollment. "
        "Only free preview lectures can be downloaded. "
        "Look for lectures marked 'Preview' in the course syllabus — "
        "their URL will contain '/preview'. "
        "To access enrolled content offline, use Coursera's official app."
    )

    PROJECT_ENROLLMENT_MESSAGE = (
        "This Coursera guided project page does not expose downloadable media in public mode. "
        "Open a course lecture preview URL with '/preview' in the path, or enroll and use Coursera's official app for offline access."
    )

    def extract(self, url: str) -> dict:
        if self.is_preview_lecture(url):
            return self.extract_preview_lecture(url)
        return self.extract_enrolled_lecture(url)

    def is_preview_lecture(self, url: str) -> bool:
        lowered = (url or "").lower()
        return "preview" in lowered or self._is_project_url(lowered)

    def extract_preview_lecture(self, url: str) -> dict:
        browser = None
        context = None

        media_urls = []
        manifest_urls = []
        subtitle_urls = []

        def handle_response(response):
            response_url = str(response.url or "")
            lowered = response_url.lower()

            if any(
                marker in lowered
                for marker in [
                    "coursera.org/files/",
                    "coursera-videos.cdn.coursera.org",
                    "d3c33hcgiwev3.cloudfront.net",
                    ".m3u8",
                    ".mp4",
                ]
            ):
                if ".m3u8" in lowered:
                    manifest_urls.append(response_url)
                elif ".mp4" in lowered:
                    media_urls.append(response_url)

            if lowered.endswith(".vtt") or "/subtitles/" in lowered:
                subtitle_urls.append(response_url)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=self.USER_AGENT_POOL[0])
                page = context.new_page()
                page.on("response", handle_response)

                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_selector("video", timeout=15000)
                except Exception:
                    pass

                page.wait_for_timeout(5000)
                html_content = page.content()

                if self._is_project_url(url) and self._is_project_enrollment_gate(page.url, html_content):
                    raise ExtractorError(self.PROJECT_ENROLLMENT_MESSAGE, platform=self.PLATFORM_ID, url=url)

                if self._looks_like_login_wall(page.url, html_content) and not self._is_project_url(url):
                    raise ExtractorError(self.ENROLLED_ERROR_MESSAGE, platform=self.PLATFORM_ID, url=url)

                media_urls.extend(self._extract_media_urls_from_page(html_content, url))
                subtitle_urls.extend(self._extract_subtitle_urls_from_page(html_content, url))

                try:
                    video_sources = page.eval_on_selector_all(
                        "video source",
                        "els => els.map(el => el.src).filter(Boolean)",
                    )
                    for source_url in video_sources:
                        if source_url:
                            if ".m3u8" in source_url.lower():
                                manifest_urls.append(source_url)
                            else:
                                media_urls.append(source_url)
                except Exception:
                    pass

                try:
                    inline_video_src = page.eval_on_selector_all(
                        "video",
                        "els => els.map(el => el.currentSrc || el.src).filter(Boolean)",
                    )
                    for source_url in inline_video_src:
                        if source_url:
                            if ".m3u8" in source_url.lower():
                                manifest_urls.append(source_url)
                            else:
                                media_urls.append(source_url)
                except Exception:
                    pass

                title = self._extract_page_title(html_content)
                author = self._extract_instructor_name(html_content)
                thumbnail = self._extract_meta_content(html_content, "og:image")
                duration = self._extract_duration_seconds(html_content)
                description = self._extract_meta_content(html_content, "og:description")

        except ExtractorError:
            raise
        except Exception as exc:
            raise ExtractorError(
                "Failed to extract Coursera preview lecture",
                platform=self.PLATFORM_ID,
                url=url,
            ) from exc
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass

        unique_media = self._unique_urls(media_urls)
        unique_manifests = self._unique_urls(manifest_urls)
        unique_subtitles = self._unique_urls(subtitle_urls)

        qualities = []
        manifest_url = unique_manifests[0] if unique_manifests else None
        is_hls = False

        if manifest_url:
            is_hls = True
            manifest_data = self._qualities_from_manifest(manifest_url, referer=url)
            qualities.extend(manifest_data.get("qualities") or [])
            subtitle_urls.extend(manifest_data.get("subtitles") or [])

        for media_url in unique_media:
            if any(item.get("url") == media_url for item in qualities):
                continue
            qualities.append(
                {
                    "label": self._label_from_media_url(media_url),
                    "url": media_url,
                    "size_bytes": None,
                    "codec": "h264",
                    "bitrate": None,
                    "hdr": False,
                    "format": "mp4",
                    "audio_note": "Single-file MP4 stream",
                }
            )

        subtitles = []
        for subtitle_url in unique_subtitles:
            subtitles.append(
                {
                    "lang": self._guess_lang_from_url(subtitle_url),
                    "label": self._guess_lang_from_url(subtitle_url),
                    "url": subtitle_url,
                    "format": "vtt",
                }
            )

        if not qualities:
            if self._is_project_url(url):
                raise ExtractorError(
                    self.PROJECT_ENROLLMENT_MESSAGE,
                    platform=self.PLATFORM_ID,
                    url=url,
                )
            raise ExtractorError(
                "Could not find publicly accessible media URLs for this Coursera preview lecture",
                platform=self.PLATFORM_ID,
                url=url,
            )

        return {
            "title": title or "Coursera Preview Lecture",
            "author": author,
            "channel_id": url,
            "thumbnail": thumbnail,
            "duration": duration,
            "view_count": None,
            "description": (description or "")[:500] if description else None,
            "upload_date": None,
            "qualities": qualities,
            "subtitles": subtitles,
            "chapters": [],
            "is_hls": is_hls,
            "manifest_url": manifest_url,
            "headers_required": {"Referer": url},
        }

    def extract_enrolled_lecture(self, url: str) -> dict:
        try:
            response = self.http_get(url)
            html_content = response.text
        except Exception as exc:
            raise ExtractorError(
                "Failed to load Coursera lecture page",
                platform=self.PLATFORM_ID,
                url=url,
            ) from exc

        if self._looks_like_login_wall(url, html_content):
            raise ExtractorError(self.ENROLLED_ERROR_MESSAGE, platform=self.PLATFORM_ID, url=url)

        raise ExtractorError(self.ENROLLED_ERROR_MESSAGE, platform=self.PLATFORM_ID, url=url)

    def _qualities_from_manifest(self, manifest_url: str, referer: str) -> dict:
        qualities = []
        subtitles = []

        try:
            response = self.http_get(
                manifest_url,
                headers={
                    **self.get_headers(),
                    "Referer": referer,
                },
            )
            parsed = m3u8.loads(response.text)

            if not parsed.playlists:
                qualities.append(
                    {
                        "label": "HLS Adaptive",
                        "url": manifest_url,
                        "size_bytes": None,
                        "codec": "h264",
                        "bitrate": None,
                        "hdr": False,
                        "format": "mp4",
                        "is_hls": True,
                        "audio_note": "Audio may be delivered as a separate HLS track",
                    }
                )
                return {"qualities": qualities, "subtitles": subtitles}

            for playlist in parsed.playlists:
                stream_info = playlist.stream_info
                resolution = getattr(stream_info, "resolution", None)
                bandwidth = getattr(stream_info, "bandwidth", None)
                codecs = getattr(stream_info, "codecs", None)

                label = "HLS"
                if resolution and len(resolution) == 2:
                    label = f"{resolution[1]}p"
                elif bandwidth:
                    label = f"{int(int(bandwidth) / 1000)}kbps"

                stream_url = playlist.absolute_uri or urljoin(manifest_url, playlist.uri)
                qualities.append(
                    {
                        "label": f"{label} (HLS)",
                        "url": stream_url,
                        "size_bytes": None,
                        "codec": codecs or "h264",
                        "bitrate": int(bandwidth) if bandwidth else None,
                        "hdr": False,
                        "format": "mp4",
                        "is_hls": True,
                        "audio_note": "Audio may be delivered as a separate HLS track",
                    }
                )

            for media_item in parsed.media:
                if media_item.type == "SUBTITLES" and media_item.uri:
                    subtitle_url = media_item.absolute_uri or urljoin(manifest_url, media_item.uri)
                    subtitles.append(subtitle_url)

        except Exception as exc:
            logger.warning("Failed to parse Coursera HLS manifest %s: %s", manifest_url, exc)
            qualities.append(
                {
                    "label": "HLS Adaptive",
                    "url": manifest_url,
                    "size_bytes": None,
                    "codec": "h264",
                    "bitrate": None,
                    "hdr": False,
                    "format": "mp4",
                    "is_hls": True,
                    "audio_note": "Audio may be delivered as a separate HLS track",
                }
            )

        return {"qualities": qualities, "subtitles": subtitles}

    def _extract_media_urls_from_page(self, html_content: str, base_url: str) -> list:
        candidates = []
        patterns = [
            r"<source[^>]+src=[\"']([^\"']+)[\"']",
            r"<video[^>]+src=[\"']([^\"']+)[\"']",
            r'"contentUrl"\s*:\s*"([^\"]+)"',
            r'"videoUrl"\s*:\s*"([^\"]+)"',
            r'"data-video-url"\s*:\s*"([^\"]+)"',
            r"data-video-url=[\"']([^\"']+)[\"']",
            r"data-src=[\"']([^\"']+\.(?:mp4|m3u8)[^\"']*)[\"']",
            r"https?://[^\s\"']+\.m3u8[^\s\"']*",
            r"https?://[^\s\"']+\.mp4[^\s\"']*",
        ]

        for pattern in patterns:
            for match in re.findall(pattern, html_content, flags=re.IGNORECASE):
                url_value = self._normalize_candidate_url(match, base_url)
                if url_value and self._looks_like_media_url(url_value):
                    candidates.append(url_value)

        return candidates

    def _extract_subtitle_urls_from_page(self, html_content: str, base_url: str) -> list:
        candidates = []
        patterns = [
            r"https?://[^\s\"']+\.vtt[^\s\"']*",
            r'"subtitles"\s*:\s*\[(.*?)\]',
            r'"captionUrl"\s*:\s*"([^\"]+)"',
        ]

        for pattern in patterns:
            for match in re.findall(pattern, html_content, flags=re.IGNORECASE | re.DOTALL):
                if "captionUrl" in pattern:
                    url_value = self._normalize_candidate_url(match, base_url)
                    if url_value:
                        candidates.append(url_value)
                    continue

                if "subtitles" in pattern:
                    payload = self._load_json_payload("[" + match + "]")
                    for subtitle_url in self._collect_urls_from_json(payload):
                        url_value = self._normalize_candidate_url(subtitle_url, base_url)
                        if url_value and url_value.lower().endswith(".vtt"):
                            candidates.append(url_value)
                    continue

                url_value = self._normalize_candidate_url(match, base_url)
                if url_value:
                    candidates.append(url_value)

        return candidates

    def _extract_page_title(self, html_content: str) -> str:
        og_title = self._extract_meta_content(html_content, "og:title")
        if og_title:
            return og_title

        h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html_content, flags=re.IGNORECASE | re.DOTALL)
        if h1_match:
            return self._clean_text(h1_match.group(1))

        title_match = re.search(r"<title>(.*?)</title>", html_content, flags=re.IGNORECASE | re.DOTALL)
        if title_match:
            return self._clean_text(title_match.group(1))

        return ""

    def _extract_instructor_name(self, html_content: str) -> str:
        patterns = [
            r'"instructorName"\s*:\s*"([^\"]+)"',
            r'"displayName"\s*:\s*"([^\"]+)"',
            r'"instructor"\s*:\s*\{[^\}]*"name"\s*:\s*"([^\"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_content, flags=re.IGNORECASE)
            if match:
                return self._clean_text(match.group(1))
        return ""

    def _extract_duration_seconds(self, html_content: str):
        patterns = [
            r'"duration"\s*:\s*"([^\"]+)"',
            r'"durationSeconds"\s*:\s*(\d+)',
            r'"videoDuration"\s*:\s*(\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_content, flags=re.IGNORECASE)
            if not match:
                continue
            value = self._clean_text(match.group(1))
            parsed = self._parse_duration(value)
            if parsed:
                return parsed
        return None

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

    def _looks_like_login_wall(self, page_url: str, html_content: str) -> bool:
        lowered_url = (page_url or "").lower()
        lowered_html = (html_content or "").lower()

        if "coursera.org/login" in lowered_url or "/signin" in lowered_url:
            return True

        markers = [
            "sign in",
            "log in",
            "start free trial",
            "enroll for free",
            "purchase subscription",
            "join for free",
            "requires enrollment",
        ]
        return any(marker in lowered_html for marker in markers)

    def _is_project_url(self, url: str) -> bool:
        return "/projects/" in (url or "").lower()

    def _is_project_enrollment_gate(self, page_url: str, html_content: str) -> bool:
        lowered_url = (page_url or "").lower()
        lowered_html = (html_content or "").lower()

        if "action=enroll" in lowered_url:
            return True
        if "authmode=signup" in lowered_url:
            return True
        if "enroll for free" in lowered_html and "<video" not in lowered_html:
            return True
        return False

    def _label_from_media_url(self, media_url: str) -> str:
        lowered = (media_url or "").lower()
        if "1080" in lowered:
            return "1080p"
        if "720" in lowered:
            return "720p"
        if "540" in lowered:
            return "540p"
        if "480" in lowered:
            return "480p"
        if "360" in lowered:
            return "360p"
        return "Direct MP4"

    def _guess_lang_from_url(self, url: str) -> str:
        lowered = (url or "").lower()
        match = re.search(r"/([a-z]{2}(?:-[a-z]{2})?)\.vtt", lowered)
        if match:
            return match.group(1)
        return "en"

    def _normalize_candidate_url(self, value: str, base_url: str) -> str:
        if not value:
            return ""
        cleaned = self._clean_text(value).replace("\\/", "/")
        if cleaned.startswith("//"):
            cleaned = "https:" + cleaned
        return urljoin(base_url, cleaned)

    def _looks_like_media_url(self, url: str) -> bool:
        lowered = (url or "").lower()
        return ".mp4" in lowered or ".m3u8" in lowered

    def _parse_duration(self, value: str):
        text = self._clean_text(value)
        if not text:
            return None

        if text.isdigit():
            return int(text)

        iso_match = re.match(r"^pt(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$", text, flags=re.IGNORECASE)
        if iso_match:
            hours = int(iso_match.group(1) or 0)
            minutes = int(iso_match.group(2) or 0)
            seconds = int(iso_match.group(3) or 0)
            return hours * 3600 + minutes * 60 + seconds

        if ":" in text:
            parts = text.split(":")
            try:
                parts_int = [int(item) for item in parts]
            except ValueError:
                return None
            if len(parts_int) == 3:
                return parts_int[0] * 3600 + parts_int[1] * 60 + parts_int[2]
            if len(parts_int) == 2:
                return parts_int[0] * 60 + parts_int[1]

        return None

    def _clean_text(self, value) -> str:
        if value is None:
            return ""
        return re.sub(r"\s+", " ", str(value)).strip()

    def _unique_urls(self, items: list) -> list:
        seen = set()
        result = []
        for item in items:
            value = (item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _load_json_payload(self, raw: str):
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _collect_urls_from_json(self, payload):
        urls = []

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    lowered_key = str(key).lower()
                    if lowered_key in {"url", "src", "contenturl", "captionurl", "vtturl"} and isinstance(value, str):
                        urls.append(value)
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        return urls
