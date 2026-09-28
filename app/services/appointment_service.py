"""Scheduling logic behind the voice agent tools: find free slots and book appointments.

A voice agent is in a live conversation while it waits for these responses, so this service
does only a couple of indexed queries per request and always produces a ready-to-speak
``message``. Business rules (opening hours, slot length) come from configuration.

All times are UTC. Slots are fixed-length and aligned to the opening hour, so a requested
start time is valid exactly when it appears in the list of generated slots for that day.
"""

import logging
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import utcnow
from app.core.errors import ConflictError, UnprocessableError
from app.models.appointment import Appointment
from app.repositories.appointment_repo import AppointmentRepository
from app.repositories.call_repo import CallRepository
from app.schemas.appointment import (
    AvailabilityRequest,
    AvailabilityResponse,
    BookingRequest,
    BookingResponse,
)

logger = logging.getLogger(__name__)

MAX_SLOTS_SPOKEN = 3  # Reading out more than a few options is tiring for a caller.


def speak_time(moment: datetime) -> str:
    """9:00 AM style, without a leading zero."""
    return moment.strftime("%I:%M %p").lstrip("0")


def speak_day(day: date) -> str:
    """'Thursday, October 1' style."""
    return f"{day:%A, %B} {day.day}"


def join_spoken(parts: list[str]) -> str:
    """['a', 'b', 'c'] -> 'a, b and c' (natural when read aloud)."""
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def as_utc(moment: datetime) -> datetime:
    """Interpret naive datetimes as UTC and convert aware ones to UTC."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


class AppointmentService:
    def __init__(
        self,
        session: Session,
        appointments: AppointmentRepository,
        calls: CallRepository,
        settings: Settings,
    ) -> None:
        self.session = session
        self.appointments = appointments
        self.calls = calls
        self.settings = settings

    @property
    def slot_length(self) -> timedelta:
        return timedelta(minutes=self.settings.appointment_slot_minutes)

    def slots_for_day(self, day: date) -> list[datetime]:
        """Every slot start time within opening hours on `day`."""
        start = datetime.combine(day, time(self.settings.business_open_hour), tzinfo=UTC)
        close = datetime.combine(day, time(), tzinfo=UTC) + timedelta(
            hours=self.settings.business_close_hour
        )
        slots = []
        while start + self.slot_length <= close:  # The whole slot must fit before closing.
            slots.append(start)
            start += self.slot_length
        return slots

    def free_slots(self, day: date) -> list[datetime]:
        """Slots on `day` that are in the future and not yet booked."""
        day_start = datetime.combine(day, time(), tzinfo=UTC)
        booked = {
            a.start_time
            for a in self.appointments.list_between(day_start, day_start + timedelta(days=1))
        }
        now = utcnow()
        return [slot for slot in self.slots_for_day(day) if slot > now and slot not in booked]

    def check_availability(self, request: AvailabilityRequest) -> AvailabilityResponse:
        free = self.free_slots(request.date)
        day = speak_day(request.date)
        if not free:
            message = f"Sorry, there are no openings on {day}. Would you like to try another day?"
        else:
            spoken = [speak_time(slot) for slot in free[:MAX_SLOTS_SPOKEN]]
            extra = " among others" if len(free) > MAX_SLOTS_SPOKEN else ""
            message = f"On {day}, I have openings at {join_spoken(spoken)}{extra}."
        return AvailabilityResponse(
            available=bool(free), date=request.date, slots=free, message=message
        )

    def book(self, request: BookingRequest) -> BookingResponse:
        start = as_utc(request.start_time)

        if start <= utcnow():
            raise UnprocessableError(
                "Sorry, that time has already passed. Could you choose a later time?",
                code="SLOT_IN_PAST",
            )
        day_slots = self.slots_for_day(start.date())
        if start not in day_slots:
            raise UnprocessableError(
                f"Sorry, appointments start every {self.settings.appointment_slot_minutes} "
                f"minutes from {speak_time(day_slots[0])} to {speak_time(day_slots[-1])}. "
                "Could you pick one of those times?",
                code="INVALID_SLOT",
            )
        if self.appointments.get_by_start_time(start):
            raise self._slot_taken(start)

        # Link the booking to the call record when the agent passes the platform call ID.
        call = self.calls.get_by_external_id(request.call_id) if request.call_id else None
        appointment = Appointment(
            customer_name=request.customer_name,
            customer_phone=request.customer_phone,
            start_time=start,
            end_time=start + self.slot_length,
            call_id=call.id if call else None,
        )
        try:
            self.appointments.add(appointment)
            self.session.commit()
        except IntegrityError:
            # Someone else booked this slot between our check and our insert.
            self.session.rollback()
            raise self._slot_taken(start) from None

        logger.info("Appointment booked", extra={"appointment_id": appointment.id})
        first_name = request.customer_name.split()[0]
        return BookingResponse(
            appointment_id=appointment.id,
            start_time=appointment.start_time,
            end_time=appointment.end_time,
            message=(
                f"You're all set, {first_name}! Your appointment is booked for "
                f"{speak_day(start.date())} at {speak_time(start)}."
            ),
        )

    def _slot_taken(self, start: datetime) -> ConflictError:
        """Build a 409 whose message offers alternatives, so the conversation can continue."""
        alternatives = [speak_time(slot) for slot in self.free_slots(start.date())[:2]]
        if alternatives:
            message = (
                "Sorry, that time is no longer available. "
                f"The closest open times that day are {join_spoken(alternatives)}."
            )
        else:
            message = "Sorry, that time is no longer available, and that day is fully booked."
        return ConflictError(message, code="SLOT_UNAVAILABLE")
