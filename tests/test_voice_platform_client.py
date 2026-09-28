"""Tests for the outbound voice platform client's retry and pagination behaviour.

``httpx.MockTransport`` answers requests with a Python function instead of the network, and a
fake ``sleep`` records the delays the client chose, so these tests are fast and deterministic.
"""

from collections.abc import Callable

import httpx
import pytest

from app.services.voice_platform_client import VoicePlatformClient, VoicePlatformError

pytestmark = pytest.mark.anyio  # Run every test in this module as an async test.


class RecordingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def make_client(
    handler: Callable[[httpx.Request], httpx.Response], sleep: RecordingSleep, **kwargs
) -> VoicePlatformClient:
    return VoicePlatformClient(
        "https://voice.test",
        "secret",
        transport=httpx.MockTransport(handler),
        sleep=sleep,
        **kwargs,
    )


def sequence(*responses: httpx.Response | Exception):
    """Handler that returns (or raises) the given items in order and counts calls."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        item = responses[calls["count"]]
        calls["count"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    return handler, calls


async def test_retries_on_5xx_then_succeeds():
    handler, calls = sequence(
        httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"id": "c1"})
    )
    sleep = RecordingSleep()

    async with make_client(handler, sleep) as client:
        result = await client.get_call("c1")

    assert result == {"id": "c1"}
    assert calls["count"] == 3
    assert len(sleep.delays) == 2
    # Equal jitter: attempt 0 waits 0.25-0.5s, attempt 1 waits 0.5-1.0s.
    assert 0.25 <= sleep.delays[0] <= 0.5
    assert 0.5 <= sleep.delays[1] <= 1.0


async def test_respects_retry_after_on_429():
    handler, calls = sequence(
        httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={})
    )
    sleep = RecordingSleep()

    async with make_client(handler, sleep) as client:
        await client.get_call("c1")

    assert sleep.delays == [7.0]
    assert calls["count"] == 2


async def test_retry_after_is_capped():
    handler, _ = sequence(
        httpx.Response(429, headers={"Retry-After": "3600"}), httpx.Response(200, json={})
    )
    sleep = RecordingSleep()

    async with make_client(handler, sleep, max_retry_after=30) as client:
        await client.get_call("c1")

    assert sleep.delays == [30]


async def test_fails_fast_on_non_retryable_4xx():
    handler, calls = sequence(httpx.Response(404), httpx.Response(200, json={}))
    sleep = RecordingSleep()

    async with make_client(handler, sleep) as client:
        with pytest.raises(VoicePlatformError) as exc_info:
            await client.get_call("missing")

    assert exc_info.value.status_code == 404
    assert calls["count"] == 1  # No retry.
    assert sleep.delays == []


async def test_retries_on_timeout():
    handler, calls = sequence(
        httpx.ReadTimeout("slow"), httpx.ConnectError("down"), httpx.Response(200, json={})
    )
    sleep = RecordingSleep()

    async with make_client(handler, sleep) as client:
        await client.get_call("c1")

    assert calls["count"] == 3
    assert len(sleep.delays) == 2


async def test_gives_up_after_max_retries():
    handler, calls = sequence(*[httpx.Response(500)] * 10)
    sleep = RecordingSleep()

    async with make_client(handler, sleep, max_retries=2) as client:
        with pytest.raises(VoicePlatformError) as exc_info:
            await client.get_call("c1")

    assert calls["count"] == 3  # One attempt plus two retries.
    assert exc_info.value.status_code == 500


async def test_gives_up_after_repeated_timeouts():
    handler, calls = sequence(*[httpx.ReadTimeout("slow")] * 10)
    sleep = RecordingSleep()

    async with make_client(handler, sleep, max_retries=1) as client:
        with pytest.raises(VoicePlatformError):
            await client.get_call("c1")

    assert calls["count"] == 2


async def test_backoff_grows_and_is_capped():
    handler, _ = sequence(*[httpx.Response(503)] * 6)
    sleep = RecordingSleep()

    async with make_client(handler, sleep, max_retries=5, backoff_max=2.0) as client:
        with pytest.raises(VoicePlatformError):
            await client.get_call("c1")

    ceilings = [0.5, 1.0, 2.0, 2.0, 2.0]  # base * 2**attempt, capped at backoff_max.
    for delay, ceiling in zip(sleep.delays, ceilings, strict=True):
        assert ceiling / 2 <= delay <= ceiling


async def test_fetch_all_calls_follows_cursor():
    pages = {
        None: {"items": [{"id": 1}, {"id": 2}], "next_cursor": "p2"},
        "p2": {"items": [{"id": 3}], "next_cursor": "p3"},
        "p3": {"items": [{"id": 4}], "next_cursor": None},
    }
    seen_cursors = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        seen_cursors.append(cursor)
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(200, json=pages[cursor])

    async with make_client(handler, RecordingSleep()) as client:
        calls = await client.fetch_all_calls(page_size=2)

    assert [c["id"] for c in calls] == [1, 2, 3, 4]
    assert seen_cursors == [None, "p2", "p3"]


async def test_fetch_all_calls_stops_runaway_pagination():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [], "next_cursor": "again"})

    async with make_client(handler, RecordingSleep()) as client:
        with pytest.raises(VoicePlatformError):
            await client.fetch_all_calls(max_pages=5)
