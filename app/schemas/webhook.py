"""Pydantic models for webhook payloads sent by the voice platform.

The payload shape is modelled on typical voice AI platforms such as Retell AI: an event type,
a unique event ID, and a ``call`` object whose fields fill in over the life of the call
(a transcript only exists after the call ends, an analysis only after it is analysed).
Unknown fields are ignored so the platform can add fields without breaking the service.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.event import EventType


class WebhookCallData(BaseModel):
    """The call object embedded in every webhook. Most fields are optional by design."""

    call_id: str = Field(min_length=1, max_length=128, description="Platform call ID.")
    agent_id: str | None = None
    from_number: str | None = None
    to_number: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    transcript: str | None = None
    summary: str | None = None
    sentiment: str | None = Field(default=None, max_length=32)


class WebhookEvent(BaseModel):
    """Top-level webhook envelope."""

    event_id: str = Field(min_length=1, max_length=128, description="Unique per delivery event.")
    event_type: EventType
    occurred_at: datetime | None = None
    call: WebhookCallData


class WebhookAck(BaseModel):
    """Response returned immediately to the platform, before processing happens."""

    received: bool = True
    duplicate: bool = Field(description="True if this event_id was already received earlier.")
