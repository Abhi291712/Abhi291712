"""Business logic for calls: creation with idempotency, pagination, status changes, summaries.

This layer knows the rules ("a call cannot go from ended back to ongoing", "the same
Idempotency-Key must not create two calls") but nothing about HTTP. It raises exceptions from
``app.core.errors``, and the exception handlers decide which status code the client sees.
"""

import base64
import binascii
import hashlib
import json
import logging
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.models.call import ALLOWED_TRANSITIONS, Call, CallStatus
from app.repositories.appointment_repo import AppointmentRepository
from app.repositories.call_repo import CallRepository
from app.repositories.event_repo import EventRepository
from app.schemas.call import (
    CallAppointmentRead,
    CallCreate,
    CallEventRead,
    CallRead,
    CallSummary,
)

logger = logging.getLogger(__name__)


def encode_cursor(call: Call) -> str:
    """Opaque cursor for the position just after `call`. Clients must not parse it."""
    raw = json.dumps({"created_at": call.created_at.isoformat(), "id": call.id})
    # URL-safe base64 without "=" padding keeps the cursor clean inside a query string.
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """Inverse of encode_cursor. Any tampered or malformed cursor becomes a 422."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)  # Restore the padding stripped above.
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return datetime.fromisoformat(data["created_at"]), str(data["id"])
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        message = "The pagination cursor is invalid."
        raise UnprocessableError(message, code="INVALID_CURSOR") from exc


def hash_request(data: CallCreate) -> str:
    """Fingerprint of a request body, used to detect an Idempotency-Key reused for new data."""
    return hashlib.sha256(data.model_dump_json().encode()).hexdigest()


class CallService:
    def __init__(
        self,
        session: Session,
        calls: CallRepository,
        events: EventRepository,
        appointments: AppointmentRepository,
    ) -> None:
        self.session = session
        self.calls = calls
        self.events = events
        self.appointments = appointments

    def create_call(self, data: CallCreate, idempotency_key: str | None) -> tuple[Call, bool]:
        """Create a call. Returns (call, replayed) where replayed=True means an earlier request
        with the same Idempotency-Key already created it and nothing new was written."""
        request_hash = hash_request(data)

        if idempotency_key:
            existing = self.calls.get_by_idempotency_key(idempotency_key)
            if existing:
                return self._replay(existing, request_hash), True

        if data.external_call_id and self.calls.get_by_external_id(data.external_call_id):
            raise ConflictError(
                f"A call with external_call_id '{data.external_call_id}' already exists.",
                code="CALL_ALREADY_EXISTS",
            )

        call = Call(
            **data.model_dump(),
            status=CallStatus.REGISTERED,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        try:
            self.calls.add(call)
            self.session.commit()
        except IntegrityError:
            # Two identical requests raced past the checks above; the unique constraints let
            # exactly one of them win. The loser returns the winner's result when possible.
            self.session.rollback()
            if idempotency_key and (existing := self.calls.get_by_idempotency_key(idempotency_key)):
                return self._replay(existing, request_hash), True
            raise ConflictError("The call already exists.", code="CALL_ALREADY_EXISTS") from None

        logger.info("Call created", extra={"call_id": call.id, "agent_id": call.agent_id})
        return call, False

    @staticmethod
    def _replay(existing: Call, request_hash: str) -> Call:
        # Same key + same body = safe retry. Same key + different body = client bug.
        if existing.request_hash != request_hash:
            raise UnprocessableError(
                "This Idempotency-Key was already used with a different request body.",
                code="IDEMPOTENCY_KEY_MISMATCH",
            )
        logger.info("Idempotent replay of call creation", extra={"call_id": existing.id})
        return existing

    def get_call(self, call_id: str) -> Call:
        call = self.calls.get(call_id)
        if call is None:
            raise NotFoundError(f"Call '{call_id}' was not found.")
        return call

    def list_calls(
        self,
        *,
        limit: int,
        status: CallStatus | None = None,
        agent_id: str | None = None,
        cursor: str | None = None,
    ) -> tuple[list[Call], str | None]:
        after = decode_cursor(cursor) if cursor else None
        # Fetch one extra row: if it exists there is another page, without a COUNT query.
        rows = self.calls.list_page(limit=limit + 1, status=status, agent_id=agent_id, after=after)
        items = rows[:limit]
        next_cursor = encode_cursor(items[-1]) if len(rows) > limit else None
        return items, next_cursor

    def update_status(self, call_id: str, new_status: CallStatus) -> Call:
        call = self.get_call(call_id)
        current = CallStatus(call.status)

        if new_status == current:
            return call  # Repeating the same PATCH is harmless (idempotent), so it is allowed.
        if new_status not in ALLOWED_TRANSITIONS[current]:
            raise ConflictError(
                f"Cannot change call status from '{current}' to '{new_status}'.",
                code="INVALID_STATUS_TRANSITION",
            )

        call.status = new_status
        now = utcnow()
        if new_status == CallStatus.ONGOING and call.started_at is None:
            call.started_at = now
        if new_status in (CallStatus.ENDED, CallStatus.ERROR) and call.ended_at is None:
            call.ended_at = now
        self.session.commit()
        logger.info("Call status changed", extra={"call_id": call.id, "status": str(new_status)})
        return call

    def get_summary(self, call_id: str) -> CallSummary:
        """Merge the call record, webhook analysis, event timeline and bookings into one view."""
        call = self.get_call(call_id)
        events = self.events.list_for_call(call.external_call_id) if call.external_call_id else []
        appointments = self.appointments.list_for_call(call.id)

        duration = None
        if call.started_at and call.ended_at:
            duration = int((call.ended_at - call.started_at).total_seconds())

        return CallSummary(
            **CallRead.model_validate(call).model_dump(),
            transcript=call.transcript,
            summary=call.summary,
            sentiment=call.sentiment,
            duration_seconds=duration,
            events=[CallEventRead.model_validate(event) for event in events],
            appointments=[CallAppointmentRead.model_validate(a) for a in appointments],
        )
