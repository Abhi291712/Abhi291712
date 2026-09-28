"""Shared pytest fixtures: an isolated test database, a configured app, and request helpers.

Every test gets a brand-new SQLite file inside pytest's temporary directory, so tests never
touch the development database and cannot affect each other. The app is built through the same
``create_app`` factory used in production, just with test settings.
"""

import json
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import compute_signature
from app.main import create_app

TEST_API_KEY = "test-api-key"
OTHER_API_KEY = "other-api-key"
WEBHOOK_SECRET = "test-webhook-secret"


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests (marked with @pytest.mark.anyio) on asyncio."""
    return "asyncio"


@pytest.fixture
def settings(tmp_path) -> Settings:
    # _env_file=None ignores any local .env so the tests behave the same on every machine.
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        api_keys=f"{TEST_API_KEY},{OTHER_API_KEY}",
        webhook_secret=WEBHOOK_SECRET,
        rate_limit_capacity=1000,  # Effectively unlimited, except in the rate limit tests.
        rate_limit_refill_per_second=100,
        business_open_hour=9,
        business_close_hour=17,
        appointment_slot_minutes=30,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # Using the client as a context manager runs the lifespan, which creates the tables.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": TEST_API_KEY}


@pytest.fixture
def db_session(app: FastAPI, client: TestClient) -> Iterator[Session]:
    """A session on the test database, for asserting on stored rows directly."""
    session = app.state.session_factory()
    yield session
    session.close()


@pytest.fixture
def call_payload() -> dict:
    return {
        "agent_id": "agent_front_desk",
        "from_number": "+14155550123",
        "to_number": "+14155550199",
    }


@pytest.fixture
def create_call(client: TestClient, auth_headers: dict, call_payload: dict) -> Callable[..., dict]:
    """Factory that creates a call through the API and returns the JSON response."""

    def _create(**overrides) -> dict:
        response = client.post("/calls", json={**call_payload, **overrides}, headers=auth_headers)
        assert response.status_code == 201, response.text
        return response.json()

    return _create


def sign(body: bytes, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> dict:
    """Headers for a correctly signed (and, by default, fresh) webhook request."""
    ts = str(int(time.time()) if timestamp is None else timestamp)
    return {
        "X-Webhook-Timestamp": ts,
        "X-Signature": f"sha256={compute_signature(body, ts, secret)}",
        "Content-Type": "application/json",
    }


@pytest.fixture
def send_webhook(client: TestClient) -> Callable[..., object]:
    """Factory that sends a signed webhook. Extra keyword arguments go into the `call` object."""

    def _send(event_type: str, call_id: str, event_id: str | None = None, **call_fields):
        payload = {
            "event_id": event_id or f"evt_{uuid.uuid4().hex}",
            "event_type": event_type,
            "call": {"call_id": call_id, "agent_id": "agent_front_desk", **call_fields},
        }
        body = json.dumps(payload).encode()
        return client.post("/webhooks/voice", content=body, headers=sign(body))

    return _send


@pytest.fixture
def future_day() -> date:
    """A date safely in the future so no slot is filtered out as 'already passed'."""
    return (datetime.now(UTC) + timedelta(days=30)).date()
