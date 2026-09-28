"""Dependency providers that wire the layers together: session -> repositories -> services.

FastAPI's ``Depends`` builds these objects for each request. Because FastAPI caches a
dependency within a single request, every repository and service created for one request
shares the same database session (and therefore the same transaction).

Keeping the wiring here means services and repositories stay plain Python classes with no
FastAPI imports, and tests can swap any piece via ``app.dependency_overrides``.
"""

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_db
from app.core.security import get_app_settings
from app.integrations.google_calendar import GoogleCalendarClient
from app.repositories.appointment_repo import AppointmentRepository
from app.repositories.call_repo import CallRepository
from app.repositories.event_repo import EventRepository
from app.services.appointment_service import AppointmentService
from app.services.call_analysis import CallAnalyzer
from app.services.call_service import CallService
from app.services.webhook_service import WebhookService


def get_calendar(request: Request) -> GoogleCalendarClient | None:
    """The external calendar client, or None when calendar sync is not configured."""
    return request.app.state.calendar


def get_call_repository(db: Session = Depends(get_db)) -> CallRepository:
    return CallRepository(db)


def get_event_repository(db: Session = Depends(get_db)) -> EventRepository:
    return EventRepository(db)


def get_appointment_repository(db: Session = Depends(get_db)) -> AppointmentRepository:
    return AppointmentRepository(db)


def get_call_service(
    db: Session = Depends(get_db),
    calls: CallRepository = Depends(get_call_repository),
    events: EventRepository = Depends(get_event_repository),
    appointments: AppointmentRepository = Depends(get_appointment_repository),
) -> CallService:
    return CallService(db, calls, events, appointments)


def get_webhook_service(
    db: Session = Depends(get_db),
    calls: CallRepository = Depends(get_call_repository),
    events: EventRepository = Depends(get_event_repository),
) -> WebhookService:
    return WebhookService(db, calls, events)


def get_appointment_service(
    db: Session = Depends(get_db),
    appointments: AppointmentRepository = Depends(get_appointment_repository),
    calls: CallRepository = Depends(get_call_repository),
    settings: Settings = Depends(get_app_settings),
    calendar: GoogleCalendarClient | None = Depends(get_calendar),
) -> AppointmentService:
    return AppointmentService(db, appointments, calls, settings, calendar)


def get_call_analyzer(request: Request) -> CallAnalyzer | None:
    """The app's LLM call analyzer, or None when LLM analysis is switched off."""
    return request.app.state.call_analyzer
