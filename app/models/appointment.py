"""ORM model for appointments booked by a voice agent during a call.

This is the "business system" the agent writes to. Appointments use fixed-length slots, so two
bookings conflict exactly when they share a start time. A unique constraint on ``start_time``
makes the database itself reject a double booking, even if two calls try to book the same slot
at the same instant.
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UTCDateTime, utcnow


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_name: Mapped[str] = mapped_column(String(100))
    customer_phone: Mapped[str] = mapped_column(String(32))
    # Unique: the last line of defence against double booking under concurrent requests.
    start_time: Mapped[datetime] = mapped_column(UTCDateTime, unique=True, index=True)
    end_time: Mapped[datetime] = mapped_column(UTCDateTime)
    # Optional link to the call during which the appointment was booked.
    call_id: Mapped[str | None] = mapped_column(ForeignKey("calls.id"), index=True)
    # ID of the matching Google Calendar event, once the booking has been synced.
    calendar_event_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
