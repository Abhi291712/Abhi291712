"""Async HTTP client for the voice platform's REST API, with production-grade retry behaviour.

External APIs fail in two different ways, and the client treats them differently:

* Transient failures (timeouts, dropped connections, 429 Too Many Requests, 5xx) often succeed
  if tried again, so they are retried with exponential backoff plus random jitter. Jitter keeps
  many clients from retrying in lock-step and overwhelming a recovering server. When the server
  sends ``Retry-After`` it knows best how long to wait, so that value is used instead.
* Other 4xx errors (bad request, unauthorised, not found) will fail the same way every time,
  so the client fails fast instead of wasting time on retries.

``transport`` and ``sleep`` can be injected, which lets the tests simulate any sequence of
responses with ``httpx.MockTransport`` and verify delays without real network calls or waiting.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class VoicePlatformError(Exception):
    """Raised when a request fails permanently or after all retries are exhausted."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class VoicePlatformClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 10.0,
        max_retry_after: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.max_retry_after = max_retry_after  # Upper bound so a bad header cannot stall us.
        self._sleep = sleep
        # One AsyncClient is reused for all requests so TCP/TLS connections are pooled.
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> "VoicePlatformClient":
        return cls(
            settings.voice_platform_base_url,
            settings.voice_platform_api_key,
            timeout=settings.voice_platform_timeout_seconds,
            max_retries=settings.voice_platform_max_retries,
            **kwargs,
        )

    async def __aenter__(self) -> "VoicePlatformClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with "equal jitter": half fixed, half random.

        attempt 0 -> 0.25-0.5s, attempt 1 -> 0.5-1s, attempt 2 -> 1-2s, ... capped at backoff_max.
        """
        ceiling = min(self.backoff_max, self.backoff_base * (2**attempt))
        return ceiling / 2 + random.uniform(0, ceiling / 2)

    def _retry_after_delay(self, response: httpx.Response) -> float | None:
        """Parse Retry-After, which may be a number of seconds or an HTTP date."""
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError):
                return None  # Unparseable header: fall back to normal backoff.
        return min(max(seconds, 0.0), self.max_retry_after)

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send a request, retrying transient failures. Returns only successful responses."""
        attempt = 0
        while True:
            try:
                response = await self._client.request(method, path, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= self.max_retries:
                    raise VoicePlatformError(
                        f"{method} {path} failed after {attempt + 1} attempts: {exc!r}"
                    ) from exc
                delay = self._backoff_delay(attempt)
                reason = type(exc).__name__
            else:
                if response.is_success:
                    return response
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    # Permanent client error: retrying would give the same answer.
                    raise VoicePlatformError(
                        f"{method} {path} returned {response.status_code}",
                        status_code=response.status_code,
                    )
                if attempt >= self.max_retries:
                    raise VoicePlatformError(
                        f"{method} {path} still returned {response.status_code} "
                        f"after {attempt + 1} attempts",
                        status_code=response.status_code,
                    )
                retry_after = self._retry_after_delay(response)
                delay = retry_after if retry_after is not None else self._backoff_delay(attempt)
                reason = f"HTTP {response.status_code}"

            logger.warning(
                "Voice platform request failed, retrying",
                extra={"path": path, "reason": reason, "attempt": attempt + 1, "delay": delay},
            )
            await self._sleep(delay)
            attempt += 1

    async def get_call(self, call_id: str) -> dict[str, Any]:
        response = await self.request("GET", f"/v1/calls/{call_id}")
        return response.json()

    async def list_calls(self, *, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
        """One page: {"items": [...], "next_cursor": "..." | null}."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = await self.request("GET", "/v1/calls", params=params)
        return response.json()

    async def fetch_all_calls(self, *, page_size: int = 100, max_pages: int = 1000) -> list[dict]:
        """Follow next_cursor until the last page (useful for backfills and reconciliation).

        `max_pages` is a safety valve: a buggy server that keeps returning a cursor must not
        trap the client in an infinite loop.
        """
        calls: list[dict] = []
        cursor: str | None = None
        for _ in range(max_pages):
            page = await self.list_calls(limit=page_size, cursor=cursor)
            calls.extend(page.get("items", []))
            cursor = page.get("next_cursor")
            if not cursor:
                return calls
        raise VoicePlatformError(f"Pagination did not finish within {max_pages} pages.")
