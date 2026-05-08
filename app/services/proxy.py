import logging
import os
import threading

import httpx


logger = logging.getLogger(__name__)


class ProxyPool:
    def __init__(self):
        self.proxies = []
        self.failed_proxies = set()
        self.current_index = 0
        self._lock = threading.Lock()
        self.load_proxies()

    def load_proxies(self):
        provider_url = os.environ.get("PROXY_PROVIDER_URL")
        if not provider_url:
            logger.info("No proxy provider configured. Running without proxies.")
            return
        headers = {}
        provider_key = os.environ.get("PROXY_PROVIDER_KEY")
        if provider_key:
            headers["Authorization"] = provider_key
        try:
            response = httpx.get(provider_url, headers=headers, timeout=20)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                self.proxies = data
            else:
                self.proxies = []
            logger.info("Loaded %s proxies", len(self.proxies))
        except Exception as exc:
            logger.warning("Failed to load proxies: %s", exc)
            self.proxies = []

    @staticmethod
    def _httpx_get_with_proxy(url: str, proxy_url: str, timeout: int = 10):
        try:
            return httpx.get(url, proxy=proxy_url, timeout=timeout)
        except TypeError:
            return httpx.get(url, proxies={"http://": proxy_url, "https://": proxy_url}, timeout=timeout)

    def get_proxy(self):
        if not self.proxies:
            return None
        with self._lock:
            attempts = 0
            while attempts < len(self.proxies):
                proxy_url = self.proxies[self.current_index % len(self.proxies)]
                self.current_index += 1
                attempts += 1
                if proxy_url in self.failed_proxies:
                    continue
                return proxy_url
            self.failed_proxies = set()
            if self.proxies:
                proxy_url = self.proxies[self.current_index % len(self.proxies)]
                self.current_index += 1
                return proxy_url
        return None

    def mark_failed(self, proxy_url: str):
        if not proxy_url:
            return
        self.failed_proxies.add(proxy_url)
        masked = proxy_url[:10]
        logger.warning("Proxy marked failed: %s...", masked)

    def health_check(self):
        if not self.proxies:
            return
        working = []
        for proxy_url in list(self.proxies):
            try:
                response = self._httpx_get_with_proxy("https://httpbin.org/ip", proxy_url, timeout=10)
                response.raise_for_status()
                working.append(proxy_url)
            except Exception:
                self.failed_proxies.add(proxy_url)
        self.proxies = working
        if len(self.failed_proxies) > len(self.proxies):
            self.load_proxies()


proxy_pool = ProxyPool()
