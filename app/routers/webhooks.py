"""HTTP endpoint that receives call lifecycle webhooks from the voice platform.

The handler does the minimum needed before answering: verify the signature (via the
``verified_webhook_body`` dependency), validate the JSON, and store the event once. The real
work is scheduled with ``BackgroundTasks`` and runs after the 200 response has been sent.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import get_session_factory
from app.core.security import verified_webhook_body
from app.dependencies import get_webhook_service
from app.schemas.webhook import WebhookAck, WebhookEvent
from app.services.webhook_service import WebhookService, process_event_in_background

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/voice", response_model=WebhookAck)
def receive_voice_webhook(
    background_tasks: BackgroundTasks,
    body: Annotated[bytes, Depends(verified_webhook_body)],  # 401 if the signature is wrong.
    service: Annotated[WebhookService, Depends(get_webhook_service)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> WebhookAck:
    # The body is parsed manually (not as a typed parameter) because the signature has to be
    # checked on the raw bytes first. Validation errors still become the standard 422.
    try:
        event = WebhookEvent.model_validate_json(body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False)) from exc

    is_new = service.record_event(event)
    if is_new:
        background_tasks.add_task(process_event_in_background, session_factory, event.event_id)
    # 200 even for duplicates: the platform only needs to know it can stop retrying.
    return WebhookAck(duplicate=not is_new)
