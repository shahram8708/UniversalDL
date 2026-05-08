import logging
import random
import re
import time
from urllib.parse import urlparse
from abc import ABC, abstractmethod
from typing import Optional

import httpx


logger = logging.getLogger(__name__)


class ExtractorError(Exception):
    def __init__(
        self,
        message: str,
        platform: Optional[str] = None,
        url: Optional[str] = None,
        data: Optional[dict] = None,
    ):
        super().__init__(message)
        self.platform = platform
        self.url = url
        self.data = data or {}


class BaseExtractor(ABC):
    PLATFORM_ID = "unknown"
    REQUIRES_HEADLESS = False
    REQUIRES_PROXY = False
    TEST_URL = ""

    USER_AGENT_POOL = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
        "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/122.0.0.0 Safari/537.36",
    ]

    def __init__(self, proxy_pool=None):
        self.proxy = proxy_pool.get_proxy() if proxy_pool else None
        self.session = None

    def get_headers(self) -> dict:
        headers = {
            "User-Agent": random.choice(self.USER_AGENT_POOL),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        if self.PLATFORM_ID and self.PLATFORM_ID != "unknown":
            headers["Referer"] = f"https://www.{self.PLATFORM_ID}.com/"
        return headers

    @abstractmethod
    def extract(self, url: str) -> dict:
        raise NotImplementedError

    def _resolve_proxy_url(self):
        if not self.proxy:
            return None
        if isinstance(self.proxy, dict):
            return self.proxy.get("https://") or self.proxy.get("http://")
        if isinstance(self.proxy, str):
            return self.proxy
        return None

    def create_http_client(self, **kwargs) -> httpx.Client:
        proxy_url = self._resolve_proxy_url()
        def build_client(params: dict) -> httpx.Client:
            if not proxy_url:
                return httpx.Client(**params)
            try:
                return httpx.Client(proxy=proxy_url, **params)
            except TypeError:
                return httpx.Client(proxies=proxy_url, **params)

        try:
            return build_client(kwargs)
        except ImportError:
            if "http2" in kwargs:
                fallback = {**kwargs, "http2": False}
                return build_client(fallback)
            raise

    def http_get(self, url: str, **kwargs) -> httpx.Response:
        base_headers = kwargs.pop("headers", None)
        retry_statuses = {403, 429, 500, 502, 503, 504}
        last_exc = None

        for attempt in range(3):
            headers = dict(base_headers) if base_headers else self.get_headers()
            headers["User-Agent"] = random.choice(self.USER_AGENT_POOL)
            try:
                with self.create_http_client(
                    headers=headers,
                    timeout=30,
                    follow_redirects=True,
                    http2=True,
                ) as client:
                    response = client.get(url, **kwargs)
                    if response.status_code >= 400:
                        if response.status_code in retry_statuses and attempt < 2:
                            time.sleep(0.6 * (attempt + 1))
                            continue
                        response.raise_for_status()
                    return response
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status_code = exc.response.status_code if exc.response else None
                if status_code in retry_statuses and attempt < 2:
                    time.sleep(0.6 * (attempt + 1))
                    continue
                break
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(0.6 * (attempt + 1))
                    continue
                break

        detail = str(last_exc) if last_exc else "unknown error"
        if last_exc and "wrong version number" in detail.lower():
            parsed = urlparse(url)
            if parsed.scheme == "https" and parsed.hostname in {"localhost", "127.0.0.1", "0.0.0.0"}:
                http_url = parsed._replace(scheme="http").geturl()
                retry_headers = dict(base_headers) if base_headers else self.get_headers()
                try:
                    with self.create_http_client(
                        headers=retry_headers,
                        timeout=30,
                        follow_redirects=True,
                        http2=False,
                    ) as client:
                        response = client.get(http_url, **kwargs)
                        response.raise_for_status()
                        return response
                except httpx.HTTPError:
                    pass
        logger.error("HTTP error for %s: %s", url, detail)
        raise ExtractorError(
            f"Failed to fetch platform data: {detail}",
            platform=self.PLATFORM_ID,
            url=url,
        ) from last_exc

    @staticmethod
    def check_drm(manifest_content: str) -> bool:
        if not manifest_content:
            return False
        markers = [
            "widevine",
            "com.widevine.alpha",
            "playready",
            "com.microsoft.playready",
            "fairplay",
            "com.apple.fairplay",
            "ext-x-key:method=sample-aes",
            "urn:mpeg:dash:mp4protection:2011",
        ]
        content = manifest_content.lower()
        for marker in markers:
            if re.search(re.escape(marker), content, re.IGNORECASE):
                return True
        return False
