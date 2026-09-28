"""Tests for Google Calendar sync. `httpx.MockTransport` plays the Google API, so no credentials
or network access are needed."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.integrations.google_calendar import GoogleCalendarClient, event_id_for, overlaps
from app.main import create_app
from app.models.appointment import Appointment
from app.services.appointment_service import sync_pending_appointments


class FakeGoogle:
    """In-memory stand-in for the two Calendar endpoints the client uses."""

    def __init__(self, busy: list[dict] | None = None, fail: bool = False):
        self.busy = busy or []
        self.fail = fail
        self.events: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        if self.fail:
            return httpx.Response(503)
        if request.url.path.endswith("/freeBusy"):
            return httpx.Response(200, json={"calendars": {"cal@example.com": {"busy": self.busy}}})
        event = json.loads(request.content)
        if event["id"] in self.events:
            return httpx.Response(409)  # Google's answer for a duplicate event ID.
        self.events[event["id"]] = event
        return httpx.Response(200, json=event)

    def client(self) -> GoogleCalendarClient:
        return GoogleCalendarClient(
            "cal@example.com",
            lambda: "test-token",
            transport=httpx.MockTransport(self.handler),
        )


@pytest.fixture
def google():
    return FakeGoogle()


@pytest.fixture
def cal_client(settings, google):
    app = create_app(settings)
    app.state.calendar = google.client()
    with TestClient(app) as test_client:
        test_client.app_state = app.state
        yield test_client


def _book(client, headers, start="2030-01-15T10:00:00Z"):
    return client.post(
        "/tools/book-appointment",
        json={"customer_name": "Jane Doe", "customer_phone": "+14155550123", "start_time": start},
        headers=headers,
    )


def test_overlaps_helper():
    from datetime import UTC, datetime

    def t(hour, minute=0):
        return datetime(2030, 1, 15, hour, minute, tzinfo=UTC)

    busy = [(t(12), t(13))]
    assert overlaps(t(12, 30), t(13), busy)
    assert overlaps(t(11, 30), t(12, 30), busy)
    assert not overlaps(t(11, 30), t(12), busy)  # Ends exactly when busy starts: fine.
    assert not overlaps(t(13), t(13, 30), busy)


def test_event_id_uses_only_allowed_characters():
    event_id = event_id_for("0f8fad5b-d9cb-469f-a165-70867728950e")
    assert set(event_id) <= set("0123456789abcdefghijklmnopqrstuv")


def test_calendar_busy_time_is_not_offered(cal_client, google, auth_headers):
    google.busy = [{"start": "2030-01-15T12:00:00Z", "end": "2030-01-15T13:00:00Z"}]

    body = cal_client.post(
        "/tools/check-availability", json={"date": "2030-01-15"}, headers=auth_headers
    ).json()

    starts = {slot[11:16] for slot in body["slots"]}
    assert "12:00" not in starts and "12:30" not in starts  # Lunch is blocked.
    assert "11:30" in starts and "13:00" in starts


def test_booking_into_calendar_busy_time_returns_409(cal_client, google, auth_headers):
    google.busy = [{"start": "2030-01-15T10:00:00Z", "end": "2030-01-15T11:00:00Z"}]

    response = _book(cal_client, auth_headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SLOT_UNAVAILABLE"


def test_booking_is_copied_to_calendar(cal_client, google, auth_headers):
    response = _book(cal_client, auth_headers)

    assert response.status_code == 201
    appointment_id = response.json()["appointment_id"]
    event = google.events[event_id_for(appointment_id)]
    assert event["summary"] == "Appointment: Jane Doe"
    assert event["start"]["dateTime"].startswith("2030-01-15T10:00:00")


def test_calendar_outage_does_not_block_scheduling(settings, auth_headers):
    down = FakeGoogle(fail=True)
    app = create_app(settings)
    app.state.calendar = down.client()

    with TestClient(app) as client:
        availability = client.post(
            "/tools/check-availability", json={"date": "2030-01-15"}, headers=auth_headers
        )
        booking = _book(client, auth_headers)

    assert availability.status_code == 200 and availability.json()["available"] is True
    assert booking.status_code == 201  # Booked locally; the calendar copy is retried later.


def test_failed_sync_is_retried_without_duplicates(settings, auth_headers):
    google = FakeGoogle(fail=True)
    app = create_app(settings)
    app.state.calendar = google.client()
    with TestClient(app) as client:
        appointment_id = _book(client, auth_headers).json()["appointment_id"]

    google.fail = False  # Google recovers.
    factory = app.state.session_factory
    synced_first = sync_pending_appointments(factory, google.client(), min_age_seconds=0)
    synced_again = sync_pending_appointments(factory, google.client(), min_age_seconds=0)

    assert synced_first == 1
    assert synced_again == 0  # Already has its event ID, so it is not picked up again.
    assert list(google.events) == [event_id_for(appointment_id)]
    with factory() as session:
        stored = session.get(Appointment, appointment_id)
        assert stored.calendar_event_id == event_id_for(appointment_id)


def test_duplicate_create_is_treated_as_success(google):
    from datetime import UTC, datetime

    client = google.client()
    kwargs = {
        "appointment_id": "0f8fad5b-d9cb-469f-a165-70867728950e",
        "summary": "x",
        "description": "y",
        "start": datetime(2030, 1, 15, 10, tzinfo=UTC),
        "end": datetime(2030, 1, 15, 10, 30, tzinfo=UTC),
    }

    first = client.create_event(**kwargs)
    second = client.create_event(**kwargs)  # e.g. a retry after a timeout.

    assert first == second
    assert len(google.events) == 1
