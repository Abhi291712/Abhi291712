"""Pydantic models describing the JSON accepted and returned by the Calls API.

Schemas are the public contract of the API. They are deliberately separate from the ORM
models: the database can gain internal columns (such as ``request_hash``) without those
leaking into responses, and incoming data is validated before it reaches any business logic.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.call import CallStatus

# E.164 phone number format: "+" followed by 7 to 15 digits, e.g. +14155550123.
E164_PATTERN = r"^\+[1-9]\d{6,14}$"


class CallCreate(BaseModel):
    """Body of POST /calls."""

    # extra="forbid" rejects unknown fields, which catches client typos early (422).
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=100, examples=["agent_front_desk"])
    from_number: str = Field(pattern=E164_PATTERN, examples=["+14155550123"])
    to_number: str = Field(pattern=E164_PATTERN, examples=["+14155550199"])
    external_call_id: str | None = Field(
        default=None, max_length=128, description="The call's ID on the voice platform, if known."
    )


class CallUpdate(BaseModel):
    """Body of PATCH /calls/{id}. Only the status can be changed by clients."""

    model_config = ConfigDict(extra="forbid")

    status: CallStatus


class CallRead(BaseModel):
    """A call as returned by the API."""

    # from_attributes lets Pydantic read values straight from an ORM object.
    model_config = ConfigDict(from_attributes=True)

    id: str
    external_call_id: str | None
    agent_id: str
    from_number: str | None
    to_number: str | None
    status: CallStatus
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CallList(BaseModel):
    """One page of calls. Pass `next_cursor` back as `cursor` to fetch the next page."""

    items: list[CallRead]
    next_cursor: str | None = Field(description="Null when there are no more results.")


class CallEventRead(BaseModel):
    """A webhook event as shown in a call summary timeline."""

    model_config = ConfigDict(from_attributes=True)

    event_id: str
    event_type: str
    status: str
    received_at: datetime


class CallAppointmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_name: str
    start_time: datetime
    end_time: datetime


class CallSummary(CallRead):
    """Everything known about a call: the record, analysis, event timeline and bookings."""

    transcript: str | None
    summary: str | None
    sentiment: str | None
    duration_seconds: int | None
    events: list[CallEventRead]
    appointments: list[CallAppointmentRead]
