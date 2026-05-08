import logging
import time
import os
import re
import asyncio
import yt_dlp
from datetime import datetime
from html import unescape as html_unescape

from playwright.sync_api import sync_playwright

from app.extractors.base import BaseExtractor, ExtractorError


logger = logging.getLogger(__name__)


class TwitterExtractor(BaseExtractor):
    PLATFORM_ID = "twitter"
    REQUIRES_HEADLESS = True
    REQUIRES_PROXY = True
    TEST_URL = "https://twitter.com/Twitter/status/1445078208190291973"

    def extract(self, url: str) -> dict:
        last_error = None
        attempts = (
            ("syndication", self._extract_with_syndication),
            ("page_html", self._extract_with_page_html),
            ("yt-dlp", self._extract_with_yt_dlp),
            ("playwright", self._extract_with_playwright),
        )

        for source_name, extractor in attempts:
            try:
                media_info = extractor(url)
                if not self._is_valid_media_info(media_info):
                    raise ExtractorError("Extractor returned invalid media metadata", platform=self.PLATFORM_ID, url=url)
                media_info.setdefault("platform", self.PLATFORM_ID)
                return media_info
            except Exception as exc:
                last_error = exc
                logger.warning("Twitter %s extraction failed for %s: %s", source_name, url, exc)

        if isinstance(last_error, ExtractorError):
            raise last_error
        if last_error is not None:
            raise ExtractorError("Failed to extract Twitter media", platform=self.PLATFORM_ID, url=url) from last_error
        raise ExtractorError("Failed to extract Twitter media", platform=self.PLATFORM_ID, url=url)

    def _extract_with_syndication(self, url: str) -> dict:
        tweet_id = self._extract_tweet_id(url)
        if not tweet_id:
            raise ExtractorError("Missing tweet id", platform=self.PLATFORM_ID, url=url)

        headers = self.get_headers()
        headers["Accept"] = "application/json, text/plain, */*"
        headers["Referer"] = "https://twitter.com/"
        headers["Origin"] = "https://twitter.com"

        endpoints = [
            f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}",
            f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&lang=en",
            f"https://cdn.syndication.twimg.com/tweet-result?tweet_id={tweet_id}",
        ]

        html_endpoints = [
            f"https://cdn.syndication.twimg.com/tweet?id={tweet_id}",
            f"https://cdn.syndication.twimg.com/tweet?id={tweet_id}&lang=en",
        ]

        last_error = None
        with self.create_http_client(headers=headers, timeout=20, follow_redirects=True) as client:
            for endpoint in endpoints:
                try:
                    response = client.get(endpoint)
                    response.raise_for_status()
                    payload = response.json()
                except Exception as exc:
                    last_error = exc
                    continue

                media_info = self._build_media_info_from_syndication(payload)
                if self._is_valid_media_info(media_info):
                    return media_info

            for endpoint in html_endpoints:
                try:
                    response = client.get(endpoint)
                    response.raise_for_status()
                    html = response.text
                except Exception as exc:
                    last_error = exc
                    continue

                media_info = self._build_media_info_from_syndication_html(html)
                if self._is_valid_media_info(media_info):
                    return media_info

        if last_error is not None:
            raise ExtractorError("Syndication API failed", platform=self.PLATFORM_ID, url=url) from last_error
        raise ExtractorError("Syndication API returned empty media", platform=self.PLATFORM_ID, url=url)

    def _extract_with_page_html(self, url: str) -> dict:
        headers = self.get_headers()
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        headers["Referer"] = "https://twitter.com/"

        candidates = [url]
        if "x.com" in url:
            candidates.append(url.replace("x.com", "twitter.com"))
            candidates.append(url.replace("x.com", "mobile.twitter.com"))
        if "twitter.com" in url and "mobile.twitter.com" not in url:
            candidates.append(url.replace("twitter.com", "mobile.twitter.com"))

        jina_prefix = "https://r.jina.ai/http://"
        for candidate in list(candidates):
            if candidate.startswith("https://"):
                candidates.append(jina_prefix + candidate[len("https://"):])

        last_error = None
        for candidate in candidates:
            try:
                response = self.http_get(candidate, headers=headers)
                html = response.text
            except Exception as exc:
                last_error = exc
                continue

            media_info = self._build_media_info_from_page_html(html)
            if self._is_valid_media_info(media_info):
                return media_info

        if last_error is not None:
            raise ExtractorError("Tweet page fetch failed", platform=self.PLATFORM_ID, url=url) from last_error
        raise ExtractorError("Tweet page returned empty media", platform=self.PLATFORM_ID, url=url)

    def _extract_with_playwright(self, url: str) -> dict:
        tweet = None

        def handle_response(response):
            nonlocal tweet
            if tweet is not None:
                return
            targets = ["/i/api/graphql/", "api.twitter.com/1.1/statuses/show.json"]
            if not any(target in response.url for target in targets):
                return
            try:
                data = response.json()
            except Exception:
                return
            tweet = self._extract_tweet(data)

        browser = None
        try:
            self._ensure_windows_proactor_policy()
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=self.USER_AGENT_POOL[0])
                page = context.new_page()
                # increase navigation timeout to reduce spurious timeouts on slow networks
                try:
                    page.set_default_navigation_timeout(60000)
                except Exception:
                    pass
                page.on("response", handle_response)

                navigation_error = None
                try:
                    page.goto(url, wait_until="networkidle", timeout=30000)
                except Exception as exc:
                    navigation_error = exc
                    # try a less strict wait strategy before giving up
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    except Exception as exc2:
                        navigation_error = exc2

                deadline = time.time() + 12
                while tweet is None and time.time() < deadline:
                    time.sleep(0.2)

                if tweet is None:
                    if navigation_error is not None:
                        raise ExtractorError("Could not extract tweet data", platform=self.PLATFORM_ID, url=url) from navigation_error
                    raise ExtractorError("Could not extract tweet data", platform=self.PLATFORM_ID, url=url)

                return self._build_media_info(tweet)
        except ExtractorError:
            raise
        except Exception as exc:
            raise ExtractorError("Failed to extract Twitter media", platform=self.PLATFORM_ID, url=url) from exc
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    logger.debug("Ignoring browser close error in twitter extractor", exc_info=True)

    def _extract_tweet(self, data):
        if not isinstance(data, dict):
            return None
        if "legacy" in data:
            return data
        thread = data.get("data", {}).get("threaded_conversation_with_injections_v2", {})
        instructions = thread.get("instructions") or []
        for instruction in instructions:
            entries = instruction.get("entries") or []
            for entry in entries:
                item = entry.get("content", {}).get("itemContent", {})
                tweet_result = item.get("tweet_results", {}).get("result")
                if tweet_result:
                    if "tweet" in tweet_result:
                        return tweet_result.get("tweet")
                    return tweet_result
        tweet_result = data.get("data", {}).get("tweetResult", {}).get("result")
        if tweet_result:
            return tweet_result
        return None

    def _extract_tweet_id(self, url: str) -> str:
        value = str(url or "").strip()
        if not value:
            return None
        match = re.search(r"/status/(\d+)", value)
        if match:
            return match.group(1)
        match = re.search(r"/statuses/(\d+)", value)
        if match:
            return match.group(1)
        if value.isdigit():
            return value
        return None

    def _build_media_info_from_syndication(self, data: dict) -> dict:
        if not isinstance(data, dict):
            return None

        title = data.get("text") or data.get("full_text") or "Tweet"
        user = data.get("user") or {}
        author = user.get("name") or user.get("screen_name")
        created_at = data.get("created_at") or data.get("created_at_millis") or data.get("created_at_ms")
        upload_date = self._parse_twitter_date(created_at)

        qualities = []
        manifest_url = None

        media_items = []
        if isinstance(data.get("mediaDetails"), list):
            media_items.extend(data.get("mediaDetails"))
        if isinstance(data.get("media"), list):
            media_items.extend(data.get("media"))
        extended = data.get("extended_entities") or {}
        if isinstance(extended.get("media"), list):
            media_items.extend(extended.get("media"))
        if isinstance(data.get("photos"), list):
            media_items.extend(data.get("photos"))

        video_block = data.get("video") or data.get("video_info") or data.get("videoInfo")
        if isinstance(video_block, dict) and isinstance(video_block.get("variants"), list):
            media_items.append({"type": "video", "video_info": video_block})

        for media in media_items:
            if not isinstance(media, dict):
                continue
            media_type = str(media.get("type") or media.get("media_type") or "").lower()
            if media_type in {"video", "animated_gif"}:
                video_info = media.get("video_info") or media.get("video") or {}
                variants = video_info.get("variants") or media.get("variants") or []
                for variant in variants:
                    if not isinstance(variant, dict):
                        continue
                    variant_url = variant.get("url") or variant.get("src")
                    if not variant_url:
                        continue
                    content_type = str(variant.get("content_type") or variant.get("type") or "").lower()
                    if "m3u8" in str(variant_url).lower():
                        if not manifest_url:
                            manifest_url = variant_url
                        continue
                    if "mp4" not in content_type and ".mp4" not in str(variant_url).lower():
                        continue
                    bitrate = variant.get("bitrate")
                    qualities.append(
                        {
                            "label": self._label_for_bitrate(bitrate),
                            "url": variant_url,
                            "size_bytes": None,
                            "codec": "h264",
                            "bitrate": bitrate,
                            "hdr": False,
                            "format": "mp4",
                            "has_audio": True,
                        }
                    )
            elif media_type in {"photo", "image"}:
                photo_url = media.get("media_url_https") or media.get("url") or media.get("media_url")
                if photo_url:
                    qualities.append(
                        {
                            "label": "image",
                            "url": photo_url,
                            "size_bytes": None,
                            "codec": None,
                            "bitrate": None,
                            "hdr": False,
                            "format": "jpg",
                            "has_audio": False,
                        }
                    )

        if not qualities and manifest_url:
            qualities.append(
                {
                    "label": "original",
                    "url": manifest_url,
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": "m3u8",
                    "has_audio": True,
                    "is_hls": True,
                }
            )

        if not qualities:
            return None

        return {
            "title": title[:200],
            "author": author,
            "channel_id": user.get("id_str") or user.get("id"),
            "thumbnail": data.get("thumbnail_url") or data.get("thumbnail") or data.get("card"),
            "duration": None,
            "view_count": None,
            "description": title[:500],
            "upload_date": upload_date,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": bool(manifest_url),
            "manifest_url": manifest_url,
            "headers_required": {"Referer": "https://twitter.com/"},
        }

    def _build_media_info_from_syndication_html(self, html: str) -> dict:
        if not html:
            return None

        text = html_unescape(str(html))
        title = "Tweet"
        author = None

        title_match = re.search(r"data-tweet-text=\"([^\"]+)\"", text)
        if title_match:
            title = title_match.group(1)
        else:
            alt_match = re.search(r"<p[^>]*class=\"tweet-text\"[^>]*>(.*?)</p>", text, re.IGNORECASE | re.DOTALL)
            if alt_match:
                cleaned = re.sub(r"<[^>]+>", "", alt_match.group(1)).strip()
                if cleaned:
                    title = cleaned

        author_match = re.search(r"data-name=\"([^\"]+)\"", text)
        if author_match:
            author = author_match.group(1)
        else:
            author_match = re.search(r"data-screen-name=\"([^\"]+)\"", text)
            if author_match:
                author = author_match.group(1)

        video_urls = re.findall(r"https?://video\.twimg\.com/[^\s\"'<>]+", text)
        image_urls = re.findall(r"https?://pbs\.twimg\.com/media/[^\s\"'<>]+", text)

        manifest_url = None
        qualities = []

        for url_value in video_urls:
            lower = str(url_value).lower()
            if ".m3u8" in lower:
                if not manifest_url:
                    manifest_url = url_value
                continue
            if ".mp4" not in lower:
                continue
            qualities.append(
                {
                    "label": "original",
                    "url": url_value,
                    "size_bytes": None,
                    "codec": "h264",
                    "bitrate": None,
                    "hdr": False,
                    "format": "mp4",
                    "has_audio": True,
                }
            )

        if not qualities and manifest_url:
            qualities.append(
                {
                    "label": "original",
                    "url": manifest_url,
                    "size_bytes": None,
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": "m3u8",
                    "has_audio": True,
                    "is_hls": True,
                }
            )

        if not qualities and image_urls:
            for image_url in image_urls:
                qualities.append(
                    {
                        "label": "image",
                        "url": image_url,
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "format": "jpg",
                        "has_audio": False,
                    }
                )

        if not qualities:
            return None

        return {
            "title": title[:200],
            "author": author,
            "channel_id": None,
            "thumbnail": image_urls[0] if image_urls else None,
            "duration": None,
            "view_count": None,
            "description": title[:500],
            "upload_date": None,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": bool(manifest_url),
            "manifest_url": manifest_url,
            "headers_required": {"Referer": "https://twitter.com/"},
        }

    def _build_media_info_from_page_html(self, html: str) -> dict:
        if not html:
            return None

        text = html_unescape(str(html))
        urls = re.findall(r"https?://[^\s\"'<>]+", text)

        video_urls = []
        image_urls = []
        manifest_url = None

        for url_value in urls:
            lower = url_value.lower()
            if "video.twimg.com" in lower and ".m3u8" in lower:
                if not manifest_url:
                    manifest_url = url_value
                continue
            if "video.twimg.com" in lower and ".mp4" in lower:
                video_urls.append(url_value)
                continue
            if "pbs.twimg.com/media/" in lower:
                image_urls.append(url_value)

        meta_images = re.findall(r"content=\"(https?://pbs\.twimg\.com/[^\"]+)\"", text)
        for url_value in meta_images:
            if url_value not in image_urls:
                image_urls.append(url_value)

        meta_videos = re.findall(r"content=\"(https?://video\.twimg\.com/[^\"]+)\"", text)
        for url_value in meta_videos:
            lower = url_value.lower()
            if ".m3u8" in lower:
                if not manifest_url:
                    manifest_url = url_value
                continue
            if ".mp4" in lower and url_value not in video_urls:
                video_urls.append(url_value)

        qualities = []

        if video_urls or manifest_url:
            for url_value in video_urls:
                qualities.append(
                    {
                        "label": "original",
                        "url": url_value,
                        "size_bytes": None,
                        "codec": "h264",
                        "bitrate": None,
                        "hdr": False,
                        "format": "mp4",
                        "has_audio": True,
                    }
                )
            if not qualities and manifest_url:
                qualities.append(
                    {
                        "label": "original",
                        "url": manifest_url,
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "format": "m3u8",
                        "has_audio": True,
                        "is_hls": True,
                    }
                )
        else:
            for url_value in image_urls:
                qualities.append(
                    {
                        "label": "image",
                        "url": url_value,
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "format": "jpg",
                        "has_audio": False,
                    }
                )

        if not qualities:
            return None

        return {
            "title": "Tweet",
            "author": None,
            "channel_id": None,
            "thumbnail": image_urls[0] if image_urls else None,
            "duration": None,
            "view_count": None,
            "description": None,
            "upload_date": None,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": bool(manifest_url),
            "manifest_url": manifest_url,
            "headers_required": {"Referer": "https://twitter.com/"},
        }

    def _parse_twitter_date(self, value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            try:
                ts = float(value)
                if ts > 10**12:
                    ts = ts / 1000
                return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OSError):
                return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%a %b %d %H:%M:%S %z %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
        try:
            return datetime.strptime(text, "%a %b %d %H:%M:%S %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            return None

    def _is_valid_media_info(self, media_info: dict) -> bool:
        if not isinstance(media_info, dict):
            return False
        title = str(media_info.get("title") or "").strip()
        qualities = media_info.get("qualities") or []
        if not title or not isinstance(qualities, list):
            return False
        for item in qualities:
            if not isinstance(item, dict):
                continue
            if str(item.get("url") or "").strip():
                return True
        return False

    def _ensure_windows_proactor_policy(self):
        if os.name != "nt":
            return
        policy_cls = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
        if policy_cls is None:
            return
        current_policy = asyncio.get_event_loop_policy()
        if isinstance(current_policy, policy_cls):
            return
        asyncio.set_event_loop_policy(policy_cls())

    def _extract_with_yt_dlp(self, url: str) -> dict:
        options = {
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
            "skip_unavailable_fragments": True,
            "http_headers": {
                "User-Agent": self.USER_AGENT_POOL[0],
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://twitter.com/",
            },
        }

        proxy_url = self._resolve_proxy_url()
        if proxy_url:
            options["proxy"] = proxy_url

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise ExtractorError(str(exc), platform=self.PLATFORM_ID, url=url) from exc

        if not isinstance(info, dict):
            raise ExtractorError("yt-dlp returned no metadata", platform=self.PLATFORM_ID, url=url)

        title = info.get("title") or "Tweet"
        thumbnail = info.get("thumbnail")
        formats = info.get("formats") or []

        best_audio_format = None
        best_audio_bitrate = 0
        for fmt in formats:
            if not fmt.get("url"):
                continue
            vcodec = str(fmt.get("vcodec") or "").lower()
            acodec = str(fmt.get("acodec") or "").lower()
            is_audio_only = vcodec == "none" and acodec not in {"", "none"}
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
        seen = set()
        manifest_url = None

        for fmt in formats:
            stream_url = fmt.get("url")
            if not stream_url:
                continue
            vcodec = str(fmt.get("vcodec") or "").lower()
            acodec = str(fmt.get("acodec") or "").lower()
            is_audio_only = vcodec == "none" and acodec not in {"", "none"}
            label = "audio_only" if is_audio_only else (str(fmt.get("height")) + "p" if fmt.get("height") else "original")
            format_ext = str(fmt.get("ext") or "").lower() or "mp4"
            selector = str(fmt.get("format_id") or f"tw_{label}_{format_ext}_{len(qualities)}")
            if selector in seen:
                continue
            seen.add(selector)

            entry = {
                "label": label,
                "selector": selector,
                "format_id": str(fmt.get("format_id")) if fmt.get("format_id") else None,
                "url": stream_url,
                "size_bytes": fmt.get("filesize") or fmt.get("filesize_approx"),
                "codec": acodec if is_audio_only else (vcodec if vcodec != "none" else acodec),
                "bitrate": fmt.get("tbr") or fmt.get("abr"),
                "hdr": False,
                "format": format_ext,
                "has_audio": True if is_audio_only else acodec not in {"", "none"},
            }

            if not is_audio_only and not entry.get("has_audio") and best_audio_url:
                entry["audio_url"] = best_audio_url
                if best_audio_size and entry.get("size_bytes"):
                    entry["size_bytes"] = entry.get("size_bytes") + best_audio_size

            if not manifest_url and (str(stream_url).lower().find(".m3u8") >= 0):
                manifest_url = stream_url
            qualities.append(entry)

        if not qualities and info.get("url"):
            single_url = info.get("url")
            if str(single_url).lower().find(".m3u8") >= 0:
                manifest_url = single_url
            qualities.append(
                {
                    "label": "original",
                    "selector": "tw_best",
                    "format_id": None,
                    "url": single_url,
                    "size_bytes": info.get("filesize") or info.get("filesize_approx"),
                    "codec": None,
                    "bitrate": None,
                    "hdr": False,
                    "format": (info.get("ext") or "mp4"),
                    "has_audio": True,
                }
            )

        if best_audio_url:
            qualities.append(
                {
                    "label": "audio_only",
                    "selector": "tw_audio_only",
                    "format_id": str(best_audio_format.get("format_id")) if best_audio_format and best_audio_format.get("format_id") else None,
                    "url": best_audio_url,
                    "size_bytes": best_audio_size,
                    "codec": "aac",
                    "bitrate": best_audio_bitrate,
                    "hdr": False,
                    "format": str(best_audio_format.get("ext") or "m4a") if best_audio_format else "m4a",
                    "has_audio": True,
                }
            )

        if not qualities:
            raise ExtractorError("No downloadable Twitter formats found", platform=self.PLATFORM_ID, url=url)

        return {
            "title": title,
            "author": info.get("uploader") or info.get("creator"),
            "channel_id": info.get("uploader_id"),
            "thumbnail": thumbnail,
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "description": (info.get("description") or "")[:500] if info.get("description") else None,
            "upload_date": None,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": bool(manifest_url),
            "manifest_url": manifest_url,
            "headers_required": {"Referer": "https://twitter.com/"},
        }

    def _build_media_info(self, tweet):
        legacy = tweet.get("legacy") or tweet.get("tweet", {}).get("legacy") or {}
        core = tweet.get("core") or tweet.get("tweet", {}).get("core") or {}
        user = core.get("user_results", {}).get("result", {}).get("legacy", {})

        title = legacy.get("full_text") or "Tweet"
        created_at = legacy.get("created_at")
        upload_date = None
        if created_at:
            try:
                upload_date = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y").strftime("%Y-%m-%d")
            except ValueError:
                upload_date = None

        qualities = []
        media_entities = (legacy.get("extended_entities") or {}).get("media") or []
        manifest_url = None
        for media in media_entities:
            if media.get("type") in {"video", "animated_gif"}:
                variants = (media.get("video_info") or {}).get("variants") or []
                for variant in variants:
                    if variant.get("content_type") != "video/mp4":
                        if not manifest_url and "m3u8" in str(variant.get("url") or "").lower():
                            manifest_url = variant.get("url")
                        continue
                    bitrate = variant.get("bitrate")
                    label = self._label_for_bitrate(bitrate)
                    qualities.append(
                        {
                            "label": label,
                            "url": variant.get("url"),
                            "size_bytes": None,
                            "codec": "h264",
                            "bitrate": bitrate,
                            "hdr": False,
                            "format": "mp4",
                            "has_audio": True,
                        }
                    )
                if not manifest_url:
                    for variant in variants:
                        if "m3u8" in str(variant.get("url") or "").lower():
                            manifest_url = variant.get("url")
                            break
            elif media.get("type") == "photo":
                qualities.append(
                    {
                        "label": "image",
                        "url": media.get("media_url_https"),
                        "size_bytes": None,
                        "codec": None,
                        "bitrate": None,
                        "hdr": False,
                        "format": "jpg",
                        "has_audio": False,
                    }
                )

        return {
            "title": title[:200],
            "author": user.get("name"),
            "channel_id": None,
            "thumbnail": (media_entities[0].get("media_url_https") if media_entities else None),
            "duration": None,
            "view_count": legacy.get("view_count") or legacy.get("retweet_count"),
            "description": title[:500],
            "upload_date": upload_date,
            "qualities": qualities,
            "subtitles": [],
            "chapters": [],
            "is_hls": bool(manifest_url),
            "manifest_url": manifest_url,
            "headers_required": {},
        }

    def _label_for_bitrate(self, bitrate):
        if not bitrate:
            return "720p"
        if bitrate >= 2500000:
            return "1080p"
        if bitrate >= 1200000:
            return "720p"
        if bitrate >= 700000:
            return "480p"
        return "360p"
