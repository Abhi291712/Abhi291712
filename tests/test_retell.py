"""Tests for the Retell AI integration: signature checks, webhook mapping and custom functions."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.core.security import compute_retell_signature, is_valid_retell_signature
from app.main import create_app
from tests.conftest import RETELL_API_KEY


def retell_post(client: TestClient, path: str, payload: dict, key: str = RETELL_API_KEY):
    body = json.dumps(payload).encode()
    signature = compute_retell_signature(body, int(time.time() * 1000), key)
    return client.post(
        path,
        content=body,
        headers={"x-retell-signature": signature, "Content-Type": "application/json"},
    )


def retell_call(**fields) -> dict:
    return {"call_id": "retell_call_1", "agent_id": "agent_retell", **fields}


# ---------- Signature ----------


def test_retell_signature_roundtrip():
    body = b'{"event":"call_started"}'
    now_ms = 1_800_000_000_000
    header = compute_retell_signature(body, now_ms, "key")

    assert is_valid_retell_signature(body, header, "key", now_ms=now_ms)
    assert not is_valid_retell_signature(body, header, "other-key", now_ms=now_ms)
    assert not is_valid_retell_signature(body + b" ", header, "key", now_ms=now_ms)
    # Older than five minutes: rejected as a possible replay.
    assert not is_valid_retell_signature(body, header, "key", now_ms=now_ms + 6 * 60 * 1000)
    assert not is_valid_retell_signature(body, "garbage", "key", now_ms=now_ms)


def test_retell_webhook_with_bad_signature_returns_401(client):
    response = retell_post(client, "/webhooks/retell", {"event": "call_started"}, key="wrong")
    assert response.status_code == 401


def test_retell_endpoints_reject_requests_when_not_configured(settings):
    unconfigured = settings.model_copy(update={"retell_api_key": None})
    with TestClient(create_app(unconfigured)) as test_client:
        response = retell_post(
            test_client, "/webhooks/retell", {"event": "call_started", "call": retell_call()}
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "RETELL_DISABLED"


# ---------- Webhooks ----------


def test_retell_call_lifecycle_updates_call(client, auth_headers):
    started = retell_post(
        client,
        "/webhooks/retell",
        {"event": "call_started", "call": retell_call(start_timestamp=1_893_456_000_000)},
    )
    retell_post(
        client,
        "/webhooks/retell",
        {
            "event": "call_ended",
            "call": retell_call(
                start_timestamp=1_893_456_000_000,
                end_timestamp=1_893_456_090_000,  # 90 seconds later.
                transcript="Agent: Hi!\nUser: I'd like to book a cleaning.",
            ),
        },
    )
    retell_post(
        client,
        "/webhooks/retell",
        {
            "event": "call_analyzed",
            "call": retell_call(
                call_analysis={
                    "call_summary": "Caller booked a cleaning.",
                    "user_sentiment": "Positive",
                }
            ),
        },
    )

    assert started.status_code == 200
    assert started.json() == {"received": True, "duplicate": False, "ignored": False}
    call = client.get("/calls", headers=auth_headers).json()["items"][0]
    summary = client.get(f"/calls/{call['id']}/summary", headers=auth_headers).json()
    assert summary["external_call_id"] == "retell_call_1"
    assert summary["agent_id"] == "agent_retell"
    assert summary["status"] == "ended"
    assert summary["duration_seconds"] == 90
    assert summary["summary"] == "Caller booked a cleaning."
    assert summary["sentiment"] == "positive"
    assert len(summary["events"]) == 3


def test_retell_retry_is_deduplicated(client):
    payload = {"event": "call_started", "call": retell_call()}

    first = retell_post(client, "/webhooks/retell", payload)
    second = retell_post(client, "/webhooks/retell", payload)

    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True


def test_unsupported_retell_event_is_acknowledged_and_ignored(client):
    response = retell_post(
        client, "/webhooks/retell", {"event": "transcript_updated", "call": retell_call()}
    )

    assert response.status_code == 200
    assert response.json()["ignored"] is True


def test_malformed_retell_webhook_returns_422(client):
    response = retell_post(client, "/webhooks/retell", {"event": "call_started"})  # No call.
    assert response.status_code == 422


# ---------- Custom functions ----------


def test_retell_check_availability_function(client):
    response = retell_post(
        client,
        "/retell/functions/check-availability",
        {"name": "check_availability", "call": retell_call(), "args": {"date": "2030-01-15"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["available"] is True
    assert body["message"].startswith("On Tuesday, January 15")


def test_retell_args_only_mode_is_supported(client):
    response = retell_post(client, "/retell/functions/check-availability", {"date": "2030-01-15"})
    assert response.json()["ok"] is True


def test_retell_booking_links_appointment_to_call(client, auth_headers):
    retell_post(client, "/webhooks/retell", {"event": "call_started", "call": retell_call()})

    response = retell_post(
        client,
        "/retell/functions/book-appointment",
        {
            "name": "book_appointment",
            "call": retell_call(),
            "args": {
                "customer_name": "Jane Doe",
                "customer_phone": "+14155550123",
                "start_time": "2030-01-15T10:00:00",
            },
        },
    )

    assert response.json()["ok"] is True
    call = client.get("/calls", headers=auth_headers).json()["items"][0]
    summary = client.get(f"/calls/{call['id']}/summary", headers=auth_headers).json()
    assert [a["customer_name"] for a in summary["appointments"]] == ["Jane Doe"]


def test_retell_double_booking_returns_speakable_200(client):
    args = {
        "customer_name": "Jane Doe",
        "customer_phone": "+14155550123",
        "start_time": "2030-01-15T10:00:00",
    }
    retell_post(client, "/retell/functions/book-appointment", {"args": args})

    response = retell_post(client, "/retell/functions/book-appointment", {"args": args})

    assert response.status_code == 200  # The agent gets a sentence, not a failed call.
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "SLOT_UNAVAILABLE"
    assert "no longer available" in body["message"]


@pytest.mark.parametrize("args", [{}, {"date": "tomorrow-ish"}])
def test_retell_function_with_bad_args_returns_speakable_200(client, args):
    response = retell_post(client, "/retell/functions/check-availability", {"args": args})

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "missing some details" in response.json()["message"]
