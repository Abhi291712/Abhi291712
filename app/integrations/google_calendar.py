"""Minimal Google Calendar client: read busy times and create events for bookings.

When configured (``GOOGLE_CALENDAR_ID`` + ``GOOGLE_SERVICE_ACCOUNT_FILE``), the scheduling tools
treat the business's real calendar as a second source of truth:

* availability excludes times that are busy in Google Calendar (lunch, holidays, meetings);
* every booking is copied into the calendar as an event, so staff see it where they work.

Only two REST endpoints are needed (``freeBusy`` and ``events.insert``), so they are called
directly with httpx instead of pulling in Google's large API client library. Authentication
uses a service account: share the calendar with the service account's e-mail address and give
it "Make changes to events" permission.

Creating events is **idempotent**: the event ID is derived from the appointment ID, so if a
sync is retried after a timeout, Google answers 409 for the existing event instead of creating
a duplicate.
"""

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from urllib.parse import quote

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/calendar/v3"
SCOPES = ["https://www.googleapis.com/auth/calendar"]


class CalendarError(Exception):
    """Raised when Google Calendar cannot be reached or rejects a request."""


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def event_id_for(appointment_id: str) -> str:
    """Google event IDs allow only the characters 0-9 and a-v; a UUID's hex digits qualify."""
    return appointment_id.replace("-", "").lower()


class GoogleCalendarClient:
    def __init__(
        self,
        calendar_id: str,
        token_provider: Callable[[], str],
        *,
        timeout: float = 3.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.calendar_id = calendar_id
        self._token_provider = token_provider
        # A short timeout: availability is checked while a caller is waiting on the phone.
        self._http = httpx.Client(base_url=API_BASE, timeout=timeout, transport=transport)

    @classmethod
    def from_settings(cls, settings: Settings) -> "GoogleCalendarClient | None":
        if not settings.calendar_enabled:
            return None
        # Imported lazily: google-auth is only needed when the integration is configured.
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(
            settings.google_service_account_file, scopes=SCOPES
        )
        lock = threading.Lock()  # Requests run in a thread pool; refresh the token only once.

        def token() -> str:
            with lock:
                if not credentials.valid:
                    credentials.refresh(Request())
                return credentials.token

        return cls(settings.google_calendar_id, token)

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self._http.request(
                method,
                path,
                headers={"Authorization": f"Bearer {self._token_provider()}"},
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise CalendarError(f"Google Calendar unreachable: {exc!r}") from exc
        return response

    def busy_intervals(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """Busy (start, end) intervals between `start` and `end`."""
        response = self._request(
            "POST",
            "/freeBusy",
            json={
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "items": [{"id": self.calendar_id}],
            },
        )
        if response.status_code != 200:
            raise CalendarError(f"freeBusy failed with HTTP {response.status_code}")
        calendar = response.json().get("calendars", {}).get(self.calendar_id, {})
        if calendar.get("errors"):
            raise CalendarError(f"freeBusy error: {calendar['errors']}")
        return [(_parse(b["start"]), _parse(b["end"])) for b in calendar.get("busy", [])]

    def create_event(
        self, *, appointment_id: str, summary: str, description: str, start: datetime, end: datetime
    ) -> str:
        """Create the event for an appointment and return its Google event ID."""
        event_id = event_id_for(appointment_id)
        response = self._request(
            "POST",
            f"/calendars/{quote(self.calendar_id, safe='')}/events",
            json={
                "id": event_id,
                "summary": summary,
                "description": description,
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": end.isoformat()},
            },
        )
        if response.status_code == 409:
            return event_id  # Already created by an earlier attempt: nothing more to do.
        if response.status_code not in (200, 201):
            raise CalendarError(f"Creating event failed with HTTP {response.status_code}")
        return response.json()["id"]


def overlaps(start: datetime, end: datetime, busy: list[tuple[datetime, datetime]]) -> bool:
    """True if [start, end) intersects any busy interval."""
    return any(start < busy_end and busy_start < end for busy_start, busy_end in busy)
