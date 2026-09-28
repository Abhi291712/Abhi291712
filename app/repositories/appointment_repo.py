"""Database access for appointments booked through the voice agent tools."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.appointment import Appointment


class AppointmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, appointment: Appointment) -> Appointment:
        self.session.add(appointment)
        self.session.flush()  # Raises IntegrityError if the slot was taken concurrently.
        return appointment

    def get_by_start_time(self, start_time: datetime) -> Appointment | None:
        return self.session.scalar(select(Appointment).where(Appointment.start_time == start_time))

    def list_between(self, start: datetime, end: datetime) -> list[Appointment]:
        """Appointments starting in the half-open interval [start, end)."""
        query = (
            select(Appointment)
            .where(Appointment.start_time >= start, Appointment.start_time < end)
            .order_by(Appointment.start_time)
        )
        return list(self.session.scalars(query))

    def list_for_call(self, call_id: str) -> list[Appointment]:
        query = (
            select(Appointment)
            .where(Appointment.call_id == call_id)
            .order_by(Appointment.start_time)
        )
        return list(self.session.scalars(query))

    def get(self, appointment_id: str) -> Appointment | None:
        return self.session.get(Appointment, appointment_id)

    def list_unsynced(
        self, *, created_before: datetime, starting_after: datetime, limit: int = 100
    ) -> list[Appointment]:
        """Upcoming appointments that were not copied to the external calendar yet."""
        query = (
            select(Appointment)
            .where(
                Appointment.calendar_event_id.is_(None),
                Appointment.created_at < created_before,
                Appointment.start_time > starting_after,
            )
            .order_by(Appointment.start_time)
            .limit(limit)
        )
        return list(self.session.scalars(query))
