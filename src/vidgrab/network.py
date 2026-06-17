from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .exceptions import NetworkError
from .utils import logger, parse_netscape_cookies, parse_rate_limit


@dataclass
class NetworkConfig:
    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    proxy: str | None = None
    rate_limit_bytes: int | None = None
    timeout: int = 30
    retries: int = 3
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    @classmethod
    def from_options(
        cls,
        cookie_file: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        rate_limit: str | None = None,
        rate_limit_bytes: int | None = None,
        timeout: int = 30,
        retries: int = 3,
    ) -> "NetworkConfig":
        config = cls(
            headers=headers or {},
            proxy=proxy,
            timeout=timeout,
            retries=retries,
        )

        if cookie_file:
            config.cookies = parse_netscape_cookies(cookie_file)

        if rate_limit_bytes is not None:
            config.rate_limit_bytes = rate_limit_bytes
        elif rate_limit:
            config.rate_limit_bytes = parse_rate_limit(rate_limit)

        if "User-Agent" not in config.headers:
            config.headers["User-Agent"] = config.user_agent

        return config

    @classmethod
    def from_app_config(cls, config: "AppConfig") -> "NetworkConfig":
        from .config import AppConfig

        cookies = config.get_cli("cookies")
        cli_headers = config.get_cli("headers", {})
        profile_headers = config.get_cli("profile_headers", {})
        all_headers = {**profile_headers, **cli_headers}

        rate_limit_bytes = config.get_cli("rate_limit_bytes")
        if rate_limit_bytes is None and config.rate_limit:
            rate_limit_bytes = parse_rate_limit(config.rate_limit)

        return cls.from_options(
            cookie_file=cookies,
            headers=all_headers,
            proxy=config.proxy,
            rate_limit_bytes=rate_limit_bytes,
            timeout=30,
            retries=3,
        )

    def to_httpx_client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "headers": self.headers,
            "cookies": self.cookies,
            "timeout": self.timeout,
            "follow_redirects": True,
        }
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return kwargs

    def to_requests_session_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "headers": self.headers,
            "cookies": self.cookies,
        }
        if self.proxy:
            kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
        return kwargs


def create_sync_session(config: NetworkConfig) -> requests.Session:
    session = requests.Session()
    for key, value in config.to_requests_session_kwargs().items():
        if key == "proxies":
            session.proxies.update(value)
        elif key == "headers":
            session.headers.update(value)
        elif key == "cookies":
            session.cookies.update(value)
    return session


def create_async_client(config: NetworkConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(**config.to_httpx_client_kwargs())


class RateLimiter:
    def __init__(self, bytes_per_second: int | None):
        self.bytes_per_second = bytes_per_second
        self._last_check: float = 0
        self._bytes_since_check: int = 0

    def _calculate_sleep(self, bytes_read: int) -> float:
        if self.bytes_per_second is None or self.bytes_per_second <= 0:
            return 0

        self._bytes_since_check += bytes_read
        now = time.time()
        elapsed = now - self._last_check

        if elapsed >= 1.0:
            self._last_check = now
            self._bytes_since_check = 0
            return 0

        expected_time = self._bytes_since_check / self.bytes_per_second
        if expected_time > elapsed:
            sleep_time = expected_time - elapsed
            self._last_check = time.time()
            self._bytes_since_check = 0
            return sleep_time
        return 0

    async def wait_if_needed(self, bytes_read: int) -> None:
        sleep_time = self._calculate_sleep(bytes_read)
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)

    def wait_if_needed_sync(self, bytes_read: int) -> None:
        sleep_time = self._calculate_sleep(bytes_read)
        if sleep_time > 0:
            time.sleep(sleep_time)


def is_retryable_error(e: Exception) -> bool:
    if isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError)):
        return True
    if isinstance(e, (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError)):
        return True
    if isinstance(e, NetworkError):
        return True
    return False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type((requests.exceptions.RequestException, httpx.HTTPError, NetworkError)),
    reraise=True,
)
def sync_get(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    try:
        response = session.get(url, **kwargs)
        if response.status_code >= 500:
            raise NetworkError(f"Server error {response.status_code} for {url}")
        response.raise_for_status()
        return response
    except requests.exceptions.RequestException as e:
        logger.debug(f"Request failed: {e}")
        raise


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type((httpx.HTTPError, NetworkError)),
    reraise=True,
)
async def async_get(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
    try:
        response = await client.get(url, **kwargs)
        if response.status_code >= 500:
            raise NetworkError(f"Server error {response.status_code} for {url}")
        response.raise_for_status()
        return response
    except httpx.HTTPError as e:
        logger.debug(f"Async request failed: {e}")
        raise


def resolve_relative_url(base_url: str, relative_url: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base_url, relative_url)


def get_content_length(session: requests.Session, url: str) -> int | None:
    try:
        response = session.head(url, allow_redirects=True, timeout=10)
        response.raise_for_status()
        length = response.headers.get("Content-Length")
        if length:
            return int(length)
    except Exception as e:
        logger.debug(f"HEAD request failed for {url}: {e}")
    return None


def supports_range_requests(session: requests.Session, url: str) -> bool:
    try:
        headers = {"Range": "bytes=0-0"}
        response = session.get(url, headers=headers, allow_redirects=True, timeout=10)
        return response.status_code == 206
    except Exception:
        return False
