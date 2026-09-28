"""ORM model for a phone call handled by a voice agent, plus the call status rules.

A ``Call`` row is the central record of the service. It can be created by an API client
(``POST /calls``) or implicitly by the first webhook the voice platform sends about a call.
The status rules live next to the model because both the API and the webhook pipeline rely on
them, and keeping them in one place prevents the two paths from drifting apart.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UTCDateTime, utcnow


class CallStatus(StrEnum):
    """Lifecycle of a call. StrEnum values serialise as plain strings in JSON and the DB."""

    REGISTERED = "registered"  # Known to the system, not yet connected.
    ONGOING = "ongoing"  # Caller and agent are talking.
    ENDED = "ended"  # Finished normally.
    ERROR = "error"  # Finished abnormally (dropped, failed to connect, ...).


# Transitions a client may request explicitly via PATCH /calls/{id}.
ALLOWED_TRANSITIONS: dict[CallStatus, set[CallStatus]] = {
    CallStatus.REGISTERED: {CallStatus.ONGOING},
    CallStatus.ONGOING: {CallStatus.ENDED, CallStatus.ERROR},
    CallStatus.ENDED: set(),  # Terminal state.
    CallStatus.ERROR: set(),  # Terminal state.
}

# How far along the lifecycle each status is. Webhooks may arrive out of order, so the webhook
# pipeline only ever moves a call to a status with a *higher* rank, never backwards.
STATUS_RANK: dict[CallStatus, int] = {
    CallStatus.REGISTERED: 0,
    CallStatus.ONGOING: 1,
    CallStatus.ENDED: 2,
    CallStatus.ERROR: 2,
}


def new_id() -> str:
    """Random, non-guessable public identifier for a call."""
    return str(uuid.uuid4())


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # The call's ID on the voice platform (e.g. a Retell call_id); links webhooks to this row.
    external_call_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    agent_id: Mapped[str] = mapped_column(String(100), index=True)
    from_number: Mapped[str | None] = mapped_column(String(32))
    to_number: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default=CallStatus.REGISTERED, index=True)

    # Idempotency: the client-supplied key plus a hash of the request body it was used with.
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))

    # Filled in from webhooks as the call progresses.
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    transcript: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    sentiment: Mapped[str | None] = mapped_column(String(32))
    # Structured LLM analysis of the transcript (intent, follow-up, extracted details).
    analysis: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    # Composite index that makes the keyset pagination query (ORDER BY created_at, id) fast.
    __table_args__ = (Index("ix_calls_created_at_id", "created_at", "id"),)
