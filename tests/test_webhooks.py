"""Tests for the webhook pipeline: signatures, deduplication, out-of-order events, summaries.

Starlette's TestClient runs background tasks before the request call returns, so by the time an
assertion runs the event has already been processed.
"""

import json
import time

from sqlalchemy import func, select

from app.models.event import Event
from tests.conftest import sign


def _get_call_by_external_id(client, auth_headers, external_id: str) -> dict:
    items = client.get("/calls", params={"limit": 100}, headers=auth_headers).json()["items"]
    return next(c for c in items if c["external_call_id"] == external_id)


def test_valid_webhook_returns_200_and_creates_call(client, auth_headers, send_webhook):
    response = send_webhook(
        "call_started", "ext-100", from_number="+14155550123", started_at="2026-01-01T10:00:00Z"
    )

    assert response.status_code == 200
    assert response.json() == {"received": True, "duplicate": False}
    call = _get_call_by_external_id(client, auth_headers, "ext-100")
    assert call["status"] == "ongoing"
    assert call["started_at"].startswith("2026-01-01T10:00:00")
    assert call["from_number"] == "+14155550123"


def test_webhook_updates_call_created_through_api(client, auth_headers, create_call, send_webhook):
    created = create_call(external_call_id="ext-api")

    send_webhook("call_started", "ext-api")

    call = client.get(f"/calls/{created['id']}", headers=auth_headers).json()
    assert call["status"] == "ongoing"


def test_bad_signature_returns_401(client):
    body = json.dumps(
        {"event_id": "e1", "event_type": "call_started", "call": {"call_id": "x"}}
    ).encode()

    response = client.post(
        "/webhooks/voice", content=body, headers=sign(body, secret="wrong-secret")
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_SIGNATURE"


def test_missing_signature_returns_401(client):
    response = client.post("/webhooks/voice", json={"event_id": "e1"})
    assert response.status_code == 401


def test_tampered_body_fails_signature(client):
    original = b'{"event_id":"e1","event_type":"call_started","call":{"call_id":"x"}}'
    tampered = original.replace(b"call_started", b"call_ended")

    response = client.post("/webhooks/voice", content=tampered, headers=sign(original))

    assert response.status_code == 401


def test_replayed_old_webhook_returns_401(client):
    # A correctly signed request captured 10 minutes ago must not be accepted again.
    body = b'{"event_id":"e1","event_type":"call_started","call":{"call_id":"x"}}'
    stale = int(time.time()) - 600

    response = client.post("/webhooks/voice", content=body, headers=sign(body, timestamp=stale))

    assert response.status_code == 401


def test_timestamp_is_part_of_the_signature(client):
    # Swapping in a fresh timestamp without re-signing must fail too.
    body = b'{"event_id":"e1","event_type":"call_started","call":{"call_id":"x"}}'
    headers = sign(body, timestamp=int(time.time()) - 600)
    headers["X-Webhook-Timestamp"] = str(int(time.time()))

    response = client.post("/webhooks/voice", content=body, headers=headers)

    assert response.status_code == 401


def test_signed_but_invalid_payload_returns_422(client):
    body = json.dumps({"event_id": "e1", "event_type": "call_exploded", "call": {}}).encode()

    response = client.post("/webhooks/voice", content=body, headers=sign(body))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_duplicate_webhook_is_ignored(client, db_session, send_webhook):
    first = send_webhook("call_started", "ext-dup", event_id="evt-dup")
    second = send_webhook("call_started", "ext-dup", event_id="evt-dup")

    assert first.json()["duplicate"] is False
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    count = db_session.scalar(select(func.count()).select_from(Event))
    assert count == 1


def test_out_of_order_ended_before_started_keeps_ended(client, auth_headers, send_webhook):
    send_webhook("call_ended", "ext-ooo", ended_at="2026-01-01T10:05:00Z", transcript="Hi...")
    send_webhook("call_started", "ext-ooo", started_at="2026-01-01T10:00:00Z")

    call = _get_call_by_external_id(client, auth_headers, "ext-ooo")
    assert call["status"] == "ended"  # The late call_started did not reopen the call.
    assert call["started_at"].startswith("2026-01-01T10:00:00")  # But its timestamp was used.


def test_analyzed_before_ended_keeps_analysis(client, auth_headers, send_webhook):
    send_webhook("call_started", "ext-an", started_at="2026-01-01T10:00:00Z")
    send_webhook("call_analyzed", "ext-an", summary="Booked a cleaning.", sentiment="positive")
    send_webhook("call_ended", "ext-an", ended_at="2026-01-01T10:03:00Z")

    call = _get_call_by_external_id(client, auth_headers, "ext-an")
    summary = client.get(f"/calls/{call['id']}/summary", headers=auth_headers).json()
    assert summary["status"] == "ended"
    assert summary["summary"] == "Booked a cleaning."
    assert summary["sentiment"] == "positive"
    assert summary["duration_seconds"] == 180


def test_summary_merges_call_events_and_appointments(
    client, auth_headers, send_webhook, future_day
):
    send_webhook("call_started", "ext-sum", started_at="2026-01-01T10:00:00Z")
    client.post(
        "/tools/book-appointment",
        json={
            "customer_name": "Jane Doe",
            "customer_phone": "+14155550123",
            "start_time": f"{future_day}T10:00:00Z",
            "call_id": "ext-sum",
        },
        headers=auth_headers,
    )
    send_webhook(
        "call_ended", "ext-sum", ended_at="2026-01-01T10:02:30Z", transcript="Agent: Hello..."
    )
    send_webhook("call_analyzed", "ext-sum", summary="Caller booked an appointment.")

    call = _get_call_by_external_id(client, auth_headers, "ext-sum")
    response = client.get(f"/calls/{call['id']}/summary", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ended"
    assert body["transcript"] == "Agent: Hello..."
    assert body["summary"] == "Caller booked an appointment."
    assert body["duration_seconds"] == 150
    assert [e["event_type"] for e in body["events"]] == [
        "call_started",
        "call_ended",
        "call_analyzed",
    ]
    assert all(e["status"] == "processed" for e in body["events"])
    assert [a["customer_name"] for a in body["appointments"]] == ["Jane Doe"]


def test_webhooks_do_not_require_api_key(send_webhook):
    # Webhooks are authenticated by signature, not by API key.
    assert send_webhook("call_started", "ext-nokey").status_code == 200
