"""HTTP endpoints a voice agent calls mid-conversation ("tools" / "functions").

When the caller says "Do you have anything on Thursday?", the voice platform calls
``/tools/check-availability``; when they confirm a time, it calls ``/tools/book-appointment``.
Every response, including errors, carries a ``message`` the agent can speak directly.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.security import require_api_key
from app.dependencies import get_appointment_service
from app.schemas.appointment import (
    AvailabilityRequest,
    AvailabilityResponse,
    BookingRequest,
    BookingResponse,
)
from app.services.appointment_service import AppointmentService

router = APIRouter(prefix="/tools", tags=["agent tools"], dependencies=[Depends(require_api_key)])

Service = Annotated[AppointmentService, Depends(get_appointment_service)]


@router.post("/check-availability", response_model=AvailabilityResponse)
def check_availability(payload: AvailabilityRequest, service: Service) -> AvailabilityResponse:
    return service.check_availability(payload)


@router.post(
    "/book-appointment", response_model=BookingResponse, status_code=status.HTTP_201_CREATED
)
def book_appointment(payload: BookingRequest, service: Service) -> BookingResponse:
    return service.book(payload)
