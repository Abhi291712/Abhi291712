"""Database access for stored webhook events.

The unique constraint on ``event_id`` does the heavy lifting for deduplication: inserting an
event that already exists raises ``IntegrityError``, which the service treats as "duplicate".
Relying on the database (instead of a check-then-insert in Python) is what makes this safe when
the platform retries a delivery while the first one is still being handled.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.models.event import Event, EventStatus


class EventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, event: Event) -> Event:
        self.session.add(event)
        self.session.flush()  # Raises IntegrityError here if event_id already exists.
        return event

    def get_by_event_id(self, event_id: str) -> Event | None:
        return self.session.scalar(select(Event).where(Event.event_id == event_id))

    def list_for_call(self, call_external_id: str) -> list[Event]:
        query = (
            select(Event)
            .where(Event.call_external_id == call_external_id)
            .order_by(Event.received_at, Event.id)
        )
        return list(self.session.scalars(query))

    def mark_processed(self, event: Event) -> None:
        event.status = EventStatus.PROCESSED
        event.processed_at = utcnow()
        event.error = None

    def mark_failed(self, event: Event, error: str) -> None:
        event.status = EventStatus.FAILED
        event.processed_at = utcnow()
        event.error = error[:2000]  # Truncate so a huge traceback cannot bloat the table.
