"""Scheduling logic behind the voice agent tools: find free slots and book appointments.

A voice agent is in a live conversation while it waits for these responses, so this service
does only a couple of indexed queries per request and always produces a ready-to-speak
``message``. Business rules (opening hours, slot length, time zone) come from configuration.

Time zones: opening hours are defined in the business's local time (``BUSINESS_TIMEZONE``).
Slots are generated in local time, stored in the database as UTC, and returned and spoken in
local time. Slots are fixed-length and aligned to the opening hour, so a requested start time
is valid exactly when it appears in the list of generated slots for that day.
"""

import logging
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core import metrics
from app.core.config import Settings
from app.core.database import utcnow
from app.core.errors import ConflictError, UnprocessableError
from app.integrations.google_calendar import CalendarError, GoogleCalendarClient, overlaps
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


def to_local(moment: datetime, tz: tzinfo) -> datetime:
    """Interpret naive datetimes as business-local time; convert aware ones to local time."""
    return moment.replace(tzinfo=tz) if moment.tzinfo is None else moment.astimezone(tz)


class AppointmentService:
    def __init__(
        self,
        session: Session,
        appointments: AppointmentRepository,
        calls: CallRepository,
        settings: Settings,
        calendar: GoogleCalendarClient | None = None,
    ) -> None:
        self.session = session
        self.appointments = appointments
        self.calls = calls
        self.settings = settings
        self.calendar = calendar  # Optional external calendar (Google), see integrations.

    @property
    def slot_length(self) -> timedelta:
        return timedelta(minutes=self.settings.appointment_slot_minutes)

    @property
    def tz(self) -> tzinfo:
        return self.settings.tz

    def slots_for_day(self, day: date) -> list[datetime]:
        """Every slot start time within opening hours on the local `day`, in local time."""
        start = datetime.combine(day, time(self.settings.business_open_hour), tzinfo=self.tz)
        close = datetime.combine(day, time(), tzinfo=self.tz) + timedelta(
            hours=self.settings.business_close_hour
        )
        slots = []
        while start + self.slot_length <= close:  # The whole slot must fit before closing.
            slots.append(start)
            start += self.slot_length
        return slots

    def _day_bounds_utc(self, day: date) -> tuple[datetime, datetime]:
        """Start and end of the local `day`, converted to UTC for database queries."""
        start = datetime.combine(day, time(), tzinfo=self.tz)
        end = datetime.combine(day + timedelta(days=1), time(), tzinfo=self.tz)
        return start.astimezone(UTC), end.astimezone(UTC)

    def booked_starts(self, day: date) -> set[datetime]:
        """UTC start times already taken on the local `day`."""
        return {a.start_time for a in self.appointments.list_between(*self._day_bounds_utc(day))}

    def calendar_busy(self, day: date) -> list[tuple[datetime, datetime]]:
        """Busy intervals from the external calendar, or [] if none is configured.

        If the calendar cannot be reached, scheduling continues with local bookings only: a
        caller waiting on the phone matters more than a perfect view of the calendar.
        """
        if self.calendar is None:
            return []
        try:
            return self.calendar.busy_intervals(*self._day_bounds_utc(day))
        except CalendarError:
            metrics.CALENDAR_SYNCS.labels("busy_lookup_failed").inc()
            logger.warning("Calendar busy lookup failed; using local bookings only", exc_info=True)
            return []

    def free_slots(self, day: date) -> list[datetime]:
        """Slots on `day` that are in the future, not booked and not busy (local time)."""
        booked = self.booked_starts(day)
        busy = self.calendar_busy(day)
        now = utcnow()
        return [
            slot
            for slot in self.slots_for_day(day)
            # Aware datetimes compare correctly across time zones, so local vs UTC is fine.
            if slot > now
            and slot.astimezone(UTC) not in booked
            and not overlaps(slot, slot + self.slot_length, busy)
        ]

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
        start = to_local(request.start_time, self.tz)

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
        if self.appointments.get_by_start_time(start.astimezone(UTC)):
            raise self._slot_taken(start)
        if overlaps(start, start + self.slot_length, self.calendar_busy(start.date())):
            raise self._slot_taken(start)  # Blocked in the business's own calendar.

        # Link the booking to the call record when the agent passes the platform call ID.
        call = self.calls.get_by_external_id(request.call_id) if request.call_id else None
        appointment = Appointment(
            customer_name=request.customer_name,
            customer_phone=request.customer_phone,
            start_time=start.astimezone(UTC),  # The database always stores UTC.
            end_time=(start + self.slot_length).astimezone(UTC),
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
            start_time=start,
            end_time=start + self.slot_length,
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


def sync_appointment_to_calendar(
    session_factory: sessionmaker[Session], calendar: GoogleCalendarClient, appointment_id: str
) -> bool:
    """Copy a booking into the external calendar. Runs as a background task after the booking
    response has been sent (and again from the retry sweeper if it failed). Returns success."""
    with session_factory() as session:
        repo = AppointmentRepository(session)
        appointment = repo.get(appointment_id)
        if appointment is None or appointment.calendar_event_id:
            return True  # Gone or already synced: nothing to do.
        try:
            event_id = calendar.create_event(
                appointment_id=appointment.id,
                summary=f"Appointment: {appointment.customer_name}",
                description=f"Booked by the voice agent. Phone: {appointment.customer_phone}",
                start=appointment.start_time,
                end=appointment.end_time,
            )
        except CalendarError:
            metrics.CALENDAR_SYNCS.labels("failed").inc()
            logger.warning(
                "Calendar sync failed; will retry", extra={"appointment_id": appointment_id}
            )
            return False
        appointment.calendar_event_id = event_id
        session.commit()
        metrics.CALENDAR_SYNCS.labels("created").inc()
        return True


def sync_pending_appointments(
    session_factory: sessionmaker[Session],
    calendar: GoogleCalendarClient,
    *,
    min_age_seconds: float,
) -> int:
    """Retry calendar sync for upcoming appointments that are not in the calendar yet."""
    now = utcnow()
    with session_factory() as session:
        pending = [
            a.id
            for a in AppointmentRepository(session).list_unsynced(
                created_before=now - timedelta(seconds=min_age_seconds), starting_after=now
            )
        ]
    return sum(sync_appointment_to_calendar(session_factory, calendar, i) for i in pending)
