"""Tests for the voice agent tool endpoints (availability and booking)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def _booking(day, time="10:00:00", **overrides) -> dict:
    return {
        "customer_name": "Jane Doe",
        "customer_phone": "+14155550123",
        "start_time": f"{day}T{time}Z",
        **overrides,
    }


# ---------- POST /tools/check-availability ----------


def test_check_availability_lists_all_slots(client, auth_headers, future_day):
    response = client.post(
        "/tools/check-availability", json={"date": str(future_day)}, headers=auth_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert len(body["slots"]) == 16  # 9:00 to 16:30 in 30-minute steps.
    assert body["message"].startswith("On ")
    assert "9:00 AM" in body["message"]


def test_check_availability_excludes_booked_slots(client, auth_headers, future_day):
    client.post(
        "/tools/book-appointment", json=_booking(future_day, "09:00:00"), headers=auth_headers
    )

    body = client.post(
        "/tools/check-availability", json={"date": str(future_day)}, headers=auth_headers
    ).json()

    assert len(body["slots"]) == 15
    assert not any(slot.startswith(f"{future_day}T09:00:00") for slot in body["slots"])
    assert "9:30 AM" in body["message"]


def test_check_availability_past_day_has_no_slots(client, auth_headers):
    past = (datetime.now(UTC) - timedelta(days=3)).date()

    body = client.post(
        "/tools/check-availability", json={"date": str(past)}, headers=auth_headers
    ).json()

    assert body["available"] is False
    assert body["slots"] == []
    assert "no openings" in body["message"]


def test_check_availability_requires_api_key_and_still_has_message(client, future_day):
    response = client.post("/tools/check-availability", json={"date": str(future_day)})

    assert response.status_code == 401
    assert response.json()["message"]  # Even errors give the agent something to say.


def test_check_availability_invalid_date_returns_422_with_message(client, auth_headers):
    response = client.post(
        "/tools/check-availability", json={"date": "next tuesday"}, headers=auth_headers
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "missing some details" in body["message"]


# ---------- POST /tools/book-appointment ----------


def test_book_appointment_returns_201_with_message(client, auth_headers, future_day):
    response = client.post(
        "/tools/book-appointment", json=_booking(future_day), headers=auth_headers
    )

    assert response.status_code == 201
    body = response.json()
    assert body["appointment_id"]
    assert body["start_time"].startswith(f"{future_day}T10:00:00")
    assert body["end_time"].startswith(f"{future_day}T10:30:00")
    assert body["message"].startswith("You're all set, Jane!")
    assert "10:00 AM" in body["message"]


def test_double_booking_returns_409_with_alternatives(client, auth_headers, future_day):
    client.post("/tools/book-appointment", json=_booking(future_day), headers=auth_headers)

    response = client.post(
        "/tools/book-appointment",
        json=_booking(future_day, customer_name="John Roe"),
        headers=auth_headers,
    )

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "SLOT_UNAVAILABLE"
    assert "no longer available" in body["message"]
    assert "9:00 AM" in body["message"]  # Offers the closest open times.


def test_booking_outside_business_hours_returns_422(client, auth_headers, future_day):
    response = client.post(
        "/tools/book-appointment", json=_booking(future_day, "20:00:00"), headers=auth_headers
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_SLOT"
    assert response.json()["message"]


def test_booking_misaligned_time_returns_422(client, auth_headers, future_day):
    response = client.post(
        "/tools/book-appointment", json=_booking(future_day, "10:15:00"), headers=auth_headers
    )
    assert response.status_code == 422


def test_booking_in_the_past_returns_422(client, auth_headers):
    past = (datetime.now(UTC) - timedelta(days=1)).date()

    response = client.post("/tools/book-appointment", json=_booking(past), headers=auth_headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SLOT_IN_PAST"


def test_booking_missing_fields_returns_422(client, auth_headers, future_day):
    response = client.post(
        "/tools/book-appointment",
        json={"start_time": f"{future_day}T10:00:00Z"},
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert response.json()["message"]


def test_booking_requires_api_key(client, future_day):
    response = client.post("/tools/book-appointment", json=_booking(future_day))
    assert response.status_code == 401


def test_booking_with_timezone_offset_is_normalised_to_utc(client, auth_headers, future_day):
    # 12:00 at UTC+02:00 is 10:00 UTC, which is a valid slot.
    response = client.post(
        "/tools/book-appointment",
        json={**_booking(future_day), "start_time": f"{future_day}T12:00:00+02:00"},
        headers=auth_headers,
    )

    assert response.status_code == 201
    assert response.json()["start_time"].startswith(f"{future_day}T10:00:00")


# ---------- Business time zone ----------


@pytest.fixture
def ny_client(settings):
    """App configured for a business in New York (UTC-5 in winter, UTC-4 in summer)."""
    ny = settings.model_copy(update={"business_timezone": "America/New_York"})
    with TestClient(create_app(ny)) as test_client:
        yield test_client


def test_slots_are_in_business_local_time(ny_client, auth_headers):
    body = ny_client.post(
        "/tools/check-availability", json={"date": "2030-01-15"}, headers=auth_headers
    ).json()

    # 9:00 AM New York time in January is 14:00 UTC; the API returns local time with offset.
    assert body["slots"][0] == "2030-01-15T09:00:00-05:00"
    assert "9:00 AM" in body["message"]


def test_booking_without_offset_uses_business_time_zone(ny_client, auth_headers):
    response = ny_client.post(
        "/tools/book-appointment",
        json=_booking("2030-07-15", "10:00:00", start_time="2030-07-15T10:00:00"),
        headers=auth_headers,
    )

    assert response.status_code == 201
    assert response.json()["start_time"] == "2030-07-15T10:00:00-04:00"  # Summer time.
    assert "10:00 AM" in response.json()["message"]


def test_booking_conflict_detected_across_offsets(ny_client, auth_headers):
    ny_client.post(
        "/tools/book-appointment",
        json=_booking("2030-01-15", start_time="2030-01-15T10:00:00"),
        headers=auth_headers,
    )

    # The same instant expressed in UTC (15:00Z) is the same slot.
    response = ny_client.post(
        "/tools/book-appointment",
        json=_booking("2030-01-15", start_time="2030-01-15T15:00:00Z"),
        headers=auth_headers,
    )

    assert response.status_code == 409
