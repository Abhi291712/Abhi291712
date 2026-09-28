"""Health check endpoint used by Docker, load balancers and uptime monitors.

Returns 200 when the service and its database are usable and 503 when the database cannot be
reached, so an orchestrator can stop routing traffic to an unhealthy instance. It requires no
authentication because infrastructure probes do not carry API keys.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import __version__
from app.core.database import get_db, ping_database

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    version: str


@router.get("/health", response_model=HealthResponse)
def health(response: Response, db: Annotated[Session, Depends(get_db)]) -> HealthResponse:
    if ping_database(db):
        return HealthResponse(status="ok", database="ok", version=__version__)
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status="degraded", database="unavailable", version=__version__)
