"""ORM model for webhook events received from the voice platform.

Every webhook is stored before it is processed. This gives the gateway:

* deduplication: ``event_id`` is unique, so a retried delivery is detected and ignored;
* an audit trail: the raw payload is kept for debugging and replay;
* visibility: ``status`` and ``error`` show whether background processing succeeded.

Events reference calls by the platform's call ID (not a foreign key) because an event can
arrive before the gateway has any record of that call.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UTCDateTime, utcnow


class EventType(StrEnum):
    CALL_STARTED = "call_started"
    CALL_ENDED = "call_ended"
    CALL_ANALYZED = "call_analyzed"


class EventStatus(StrEnum):
    RECEIVED = "received"  # Stored, waiting for background processing.
    PROCESSED = "processed"  # Applied to the call record.
    FAILED = "failed"  # Processing raised an error; see the `error` column.


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Unique constraint = database-level guarantee that the same event is stored only once.
    event_id: Mapped[str] = mapped_column(String(128), unique=True)
    event_type: Mapped[str] = mapped_column(String(32))
    call_external_id: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default=EventStatus.RECEIVED)
    error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
