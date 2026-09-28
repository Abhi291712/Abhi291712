"""Tests for durable webhook processing: events that were never processed, or that failed,
are retried by the sweeper until they succeed or reach the attempt limit."""

from datetime import timedelta

import pytest

from app.core.database import utcnow
from app.models.event import Event, EventStatus
from app.schemas.webhook import WebhookEvent
from app.services import webhook_service
from app.services.webhook_service import build_webhook_service, reprocess_pending_events


def _store_without_processing(session, event_id: str, call_id: str) -> None:
    """Simulate a crash: the event is stored but the background task never runs."""
    event = WebhookEvent.model_validate(
        {"event_id": event_id, "event_type": "call_started", "call": {"call_id": call_id}}
    )
    assert build_webhook_service(session).record_event(event)
    stored = session.query(Event).filter_by(event_id=event_id).one()
    stored.received_at = utcnow() - timedelta(minutes=5)  # Old enough to be retried.
    session.commit()


def test_unprocessed_event_is_picked_up_by_sweeper(app, db_session, client, auth_headers):
    _store_without_processing(db_session, "evt-lost", "ext-lost")

    retried = reprocess_pending_events(
        app.state.session_factory, min_age_seconds=30, max_attempts=5
    )

    assert retried == 1
    db_session.expire_all()
    stored = db_session.query(Event).filter_by(event_id="evt-lost").one()
    assert stored.status == EventStatus.PROCESSED
    assert stored.attempts == 1
    calls = client.get("/calls", headers=auth_headers).json()["items"]
    assert [c["external_call_id"] for c in calls] == ["ext-lost"]


def test_recent_events_are_left_to_the_normal_background_task(app, db_session):
    event = WebhookEvent.model_validate(
        {"event_id": "evt-new", "event_type": "call_started", "call": {"call_id": "x"}}
    )
    build_webhook_service(db_session).record_event(event)

    assert (
        reprocess_pending_events(app.state.session_factory, min_age_seconds=30, max_attempts=5) == 0
    )


def test_failed_event_is_retried_until_max_attempts(app, db_session, monkeypatch):
    _store_without_processing(db_session, "evt-bad", "ext-bad")

    def always_fail(self, event):
        raise RuntimeError("downstream unavailable")

    monkeypatch.setattr(webhook_service.WebhookService, "_apply", always_fail)
    for _ in range(5):
        reprocess_pending_events(app.state.session_factory, min_age_seconds=0, max_attempts=3)

    db_session.expire_all()
    stored = db_session.query(Event).filter_by(event_id="evt-bad").one()
    assert stored.status == EventStatus.FAILED
    assert stored.attempts == 3  # Stopped at the limit instead of retrying forever.
    assert "downstream unavailable" in stored.error


def test_failed_event_succeeds_on_retry(app, db_session, monkeypatch):
    _store_without_processing(db_session, "evt-flaky", "ext-flaky")
    original = webhook_service.WebhookService._apply
    calls = {"n": 0}

    def fail_once(self, event):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("temporary")
        return original(self, event)

    monkeypatch.setattr(webhook_service.WebhookService, "_apply", fail_once)
    reprocess_pending_events(app.state.session_factory, min_age_seconds=0, max_attempts=5)
    reprocess_pending_events(app.state.session_factory, min_age_seconds=0, max_attempts=5)

    db_session.expire_all()
    stored = db_session.query(Event).filter_by(event_id="evt-flaky").one()
    assert stored.status == EventStatus.PROCESSED
    assert stored.attempts == 2
    assert stored.error is None


@pytest.mark.anyio
async def test_sweeper_loop_runs_and_stops(app, settings, monkeypatch):
    import asyncio

    from app.services import event_sweeper

    runs = []
    monkeypatch.setattr(
        event_sweeper, "reprocess_pending_events", lambda *a, **k: runs.append(1) or 0
    )
    fast = settings.model_copy(update={"event_retry_interval_seconds": 0.01})

    task = asyncio.create_task(event_sweeper.run_event_sweeper(app.state.session_factory, fast))
    await asyncio.sleep(0.1)
    task.cancel()

    assert runs  # It ran at least once and was cancellable.
