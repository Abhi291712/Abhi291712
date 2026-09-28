"""HTTP endpoints called by Retell AI: call webhooks and the agent's custom functions.

All requests are authenticated with Retell's ``x-retell-signature`` header rather than an API
key, because Retell signs everything it sends with the account's API key.

Custom function endpoints always answer ``200`` with an ``ok`` flag and a ``message``. Retell
passes the response body to the agent's LLM, which then decides what to say; an HTTP error
would be reported as a failed function call instead of giving the agent a sentence it can use
("that slot was just taken, how about 10:30?"). The plain ``/tools/*`` endpoints keep their
REST status codes (409, 422, ...) for other clients.
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import get_session_factory
from app.core.errors import AppError
from app.core.security import verified_retell_body
from app.dependencies import get_appointment_service, get_webhook_service
from app.integrations.retell import (
    SUPPORTED_EVENTS,
    RetellWebhook,
    extract_function_args,
    to_webhook_event,
)
from app.schemas.appointment import AvailabilityRequest, BookingRequest
from app.services.appointment_service import AppointmentService
from app.services.webhook_service import WebhookService, process_event_in_background

router = APIRouter(tags=["retell"])

RetellBody = Annotated[bytes, Depends(verified_retell_body)]  # 401 if the signature is wrong.
Appointments = Annotated[AppointmentService, Depends(get_appointment_service)]

MISSING_DETAILS = "Sorry, I'm missing some details for that. Could you repeat them?"


class RetellWebhookAck(BaseModel):
    received: bool = True
    duplicate: bool = False
    ignored: bool = False


def _parse_json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except ValueError as exc:
        raise RequestValidationError(
            [{"loc": ("body",), "msg": "Invalid JSON", "type": "json_invalid"}]
        ) from exc


@router.post("/webhooks/retell", response_model=RetellWebhookAck)
def receive_retell_webhook(
    background_tasks: BackgroundTasks,
    body: RetellBody,
    service: Annotated[WebhookService, Depends(get_webhook_service)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> RetellWebhookAck:
    try:
        webhook = RetellWebhook.model_validate(_parse_json(body))
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False)) from exc

    if webhook.event not in SUPPORTED_EVENTS:
        return RetellWebhookAck(ignored=True)  # Acknowledge so Retell does not retry.

    event = to_webhook_event(webhook)
    is_new = service.record_event(event)  # Same pipeline as the generic webhook.
    if is_new:
        background_tasks.add_task(process_event_in_background, session_factory, event.event_id)
    return RetellWebhookAck(duplicate=not is_new)


def _function_result(run) -> dict[str, Any]:
    """Run a tool and turn any expected failure into a speakable 200 response."""
    try:
        return {"ok": True, **run().model_dump(mode="json")}
    except ValidationError:
        return {"ok": False, "error_code": "VALIDATION_ERROR", "message": MISSING_DETAILS}
    except AppError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}


@router.post("/retell/functions/check-availability")
def retell_check_availability(body: RetellBody, service: Appointments) -> dict[str, Any]:
    args = extract_function_args(_parse_json(body))
    return _function_result(
        lambda: service.check_availability(AvailabilityRequest.model_validate(args))
    )


@router.post("/retell/functions/book-appointment")
def retell_book_appointment(body: RetellBody, service: Appointments) -> dict[str, Any]:
    args = extract_function_args(_parse_json(body))
    return _function_result(lambda: service.book(BookingRequest.model_validate(args)))
