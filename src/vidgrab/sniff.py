from __future__ import annotations

import json
import re
from typing import Any

from .exceptions import PlaywrightMissingError
from .models import StreamType, VideoStream
from .network import NetworkConfig, resolve_relative_url


class WebSniffer:
    def __init__(self, network_config: NetworkConfig):
        self.network_config = network_config
        self._playwright_available = False
        self._browser = None
        self._context = None
        self._page = None
        self._init_browser()

    def _init_browser(self) -> None:
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._playwright_available = True
        except ImportError:
            raise PlaywrightMissingError()

    def close(self) -> None:
        if self._page:
            self._page.close()
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if hasattr(self, "_playwright") and self._playwright:
            self._playwright.stop()

    def _get_browser(self):
        if not self._playwright_available:
            raise PlaywrightMissingError()

        if not self._browser:
            launch_args: dict[str, Any] = {"headless": True}
            if self.network_config.proxy:
                launch_args["proxy"] = {"server": self.network_config.proxy}
            self._browser = self._playwright.chromium.launch(**launch_args)

            context_args: dict[str, Any] = {}
            if self.network_config.cookies:
                cookies_list = [
                    {"name": name, "value": value, "domain": ".", "path": "/"}
                    for name, value in self.network_config.cookies.items()
                ]
                context_args["cookies"] = cookies_list
            if self.network_config.headers:
                context_args["extra_http_headers"] = self.network_config.headers

            self._context = self._browser.new_context(**context_args)
            self._page = self._context.new_page()

        return self._page

    def sniff(self, url: str) -> list[VideoStream]:
        page = self._get_browser()
        requests: list[dict[str, Any]] = []

        def handle_request(request):
            req_url = request.url
            if re.search(r"\.(m3u8?|mp4|webm|ts|m4v)\b", req_url, re.IGNORECASE):
                requests.append({
                    "url": req_url,
                    "method": request.method,
                    "resource_type": request.resource_type,
                    "headers": dict(request.headers),
                })

        page.on("request", handle_request)
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)

        html = page.content()
        streams = self._extract_streams(requests, html, url)
        return streams

    def _extract_streams(self, requests: list[dict[str, Any]], html: str, base_url: str) -> list[VideoStream]:
        streams: list[VideoStream] = []
        seen_urls: set[str] = set()

        for req in requests:
            url = req["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)

            if url.endswith(".m3u8") or url.endswith(".m3u"):
                streams.append(VideoStream(
                    url=url,
                    stream_type=StreamType.M3U8,
                    quality="sniffed",
                    ext="ts",
                ))
            elif re.search(r"\.(mp4|webm|mkv|mov|ts)\b", url, re.IGNORECASE):
                ext = re.search(r"\.(mp4|webm|mkv|mov|ts)\b", url, re.IGNORECASE).group(1).lower()
                streams.append(VideoStream(
                    url=url,
                    stream_type=StreamType.DIRECT,
                    quality="sniffed",
                    ext=ext,
                ))

        patterns = [
            r'["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'["\']([^"\']+\.m3u[^"\']*)["\']',
            r'src=["\']([^"\']+\.mp4[^"\']*)["\']',
            r'src=["\']([^"\']+\.webm[^"\']*)["\']',
            r'videoUrl["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            r'source["\']?\s*[:=]\s*["\']([^"\']+\.m3u8?[^"\']*)["\']',
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, html):
                url = match.group(1)
                if not url.startswith("http"):
                    url = resolve_relative_url(base_url, url)
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                if ".m3u8" in url or ".m3u" in url:
                    streams.append(VideoStream(
                        url=url,
                        stream_type=StreamType.M3U8,
                        quality="sniffed",
                        ext="ts",
                    ))
                elif re.search(r"\.(mp4|webm|mkv|mov|ts)\b", url, re.IGNORECASE):
                    ext = re.search(r"\.(mp4|webm|mkv|mov|ts)\b", url, re.IGNORECASE).group(1).lower()
                    streams.append(VideoStream(
                        url=url,
                        stream_type=StreamType.DIRECT,
                        quality="sniffed",
                        ext=ext,
                    ))

        return streams

    def sniff_and_print(self, url: str, download: bool = False) -> list[VideoStream]:
        streams = self.sniff(url)
        output = []
        for i, s in enumerate(streams, 1):
            output.append({
                "index": i,
                "url": s.url,
                "type": s.stream_type.value,
                "quality": s.quality,
                "encrypted": s.is_encrypted,
            })
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return streams
