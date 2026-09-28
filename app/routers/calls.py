"""HTTP endpoints for call records: create, fetch, list, update status and summary.

Each function here only translates between HTTP and the service layer: read parameters,
call ``CallService``, and return the result with the right status code. Validation happens in
the Pydantic schemas, rules in the service, and error formatting in the exception handlers.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status

from app.core.security import require_api_key
from app.dependencies import get_call_service
from app.models.call import CallStatus
from app.schemas.call import CallCreate, CallList, CallRead, CallSummary, CallUpdate
from app.services.call_service import CallService

# `dependencies=[...]` applies API key auth to every route in this router.
router = APIRouter(prefix="/calls", tags=["calls"], dependencies=[Depends(require_api_key)])

Service = Annotated[CallService, Depends(get_call_service)]


@router.post("", response_model=CallRead, status_code=status.HTTP_201_CREATED)
def create_call(
    payload: CallCreate,
    response: Response,
    service: Service,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            max_length=255,
            description="Unique key per logical request; retries with the same key are safe.",
        ),
    ] = None,
) -> CallRead:
    call, replayed = service.create_call(payload, idempotency_key)
    if replayed:
        # Same status and body as the original, plus a hint that nothing new was created.
        response.headers["Idempotent-Replayed"] = "true"
    return call


@router.get("", response_model=CallList)
def list_calls(
    service: Service,
    status_filter: Annotated[CallStatus | None, Query(alias="status")] = None,
    agent_id: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> CallList:
    items, next_cursor = service.list_calls(
        limit=limit, status=status_filter, agent_id=agent_id, cursor=cursor
    )
    return CallList(items=items, next_cursor=next_cursor)


@router.get("/{call_id}", response_model=CallRead)
def get_call(call_id: str, service: Service) -> CallRead:
    return service.get_call(call_id)


@router.get("/{call_id}/summary", response_model=CallSummary)
def get_call_summary(call_id: str, service: Service) -> CallSummary:
    return service.get_summary(call_id)


@router.patch("/{call_id}", response_model=CallRead)
def update_call(call_id: str, payload: CallUpdate, service: Service) -> CallRead:
    return service.update_status(call_id, payload.status)
