"""Tests for the token bucket rate limiter, both as a unit and through the HTTP stack."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.middleware.rate_limit import RateLimiter
from tests.conftest import OTHER_API_KEY, TEST_API_KEY


class FakeClock:
    """Manually advanced clock so refill behaviour can be tested without sleeping."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_bucket_allows_burst_then_blocks():
    limiter = RateLimiter(capacity=3, refill_rate=1.0, clock=FakeClock())

    results = [limiter.acquire("k")[0] for _ in range(4)]

    assert results == [True, True, True, False]


def test_bucket_refills_over_time():
    clock = FakeClock()
    limiter = RateLimiter(capacity=2, refill_rate=0.5, clock=clock)  # One token every 2 seconds.
    limiter.acquire("k")
    limiter.acquire("k")

    allowed, wait = limiter.acquire("k")
    assert not allowed and wait == pytest.approx(2.0)

    clock.now += 2.0
    assert limiter.acquire("k")[0] is True


def test_bucket_never_exceeds_capacity():
    clock = FakeClock()
    limiter = RateLimiter(capacity=2, refill_rate=10.0, clock=clock)
    clock.now += 1000  # A long idle period must not bank thousands of tokens.

    results = [limiter.acquire("k")[0] for _ in range(3)]

    assert results == [True, True, False]


@pytest.fixture
def limited_client(settings):
    strict = settings.model_copy(
        update={"rate_limit_capacity": 2, "rate_limit_refill_per_second": 0.1}
    )
    with TestClient(create_app(strict)) as test_client:
        yield test_client


def test_exceeding_limit_returns_429_with_retry_after(limited_client):
    headers = {"X-API-Key": TEST_API_KEY}

    statuses = [limited_client.get("/calls", headers=headers).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]
    response = limited_client.get("/calls", headers=headers)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert response.json()["error"]["code"] == "RATE_LIMITED"


def test_each_api_key_has_its_own_bucket(limited_client):
    for _ in range(3):
        limited_client.get("/calls", headers={"X-API-Key": TEST_API_KEY})

    response = limited_client.get("/calls", headers={"X-API-Key": OTHER_API_KEY})

    assert response.status_code == 200


def test_rate_limited_tool_response_includes_spoken_message(limited_client):
    headers = {"X-API-Key": TEST_API_KEY}
    for _ in range(2):
        limited_client.post(
            "/tools/check-availability", json={"date": "2030-01-01"}, headers=headers
        )

    response = limited_client.post(
        "/tools/check-availability", json={"date": "2030-01-01"}, headers=headers
    )

    assert response.status_code == 429
    assert "try again" in response.json()["message"]


def test_invalid_keys_are_not_rate_limited_but_rejected(limited_client):
    statuses = [
        limited_client.get("/calls", headers={"X-API-Key": "bogus"}).status_code for _ in range(5)
    ]
    assert statuses == [401] * 5


def test_health_is_not_rate_limited(limited_client):
    statuses = [limited_client.get("/health").status_code for _ in range(5)]
    assert statuses == [200] * 5
