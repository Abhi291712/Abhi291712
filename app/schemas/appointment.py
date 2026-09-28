"""Pydantic models for the voice agent tool endpoints (availability and booking).

Tool endpoints are called by a voice agent in the middle of a live conversation, so every
response includes a ``message`` field: a complete sentence the agent can read aloud without any
further processing. Structured fields (slots, IDs) are also returned for agents that want them.
"""

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.call import E164_PATTERN


class AvailabilityRequest(BaseModel):
    """Body of POST /tools/check-availability."""

    model_config = ConfigDict(extra="ignore")  # Agents often send extra context; ignore it.

    # `dt.date` (not `date`) avoids a name clash between the field and its type.
    date: dt.date = Field(description="Day to check, YYYY-MM-DD (UTC).")
    call_id: str | None = Field(default=None, description="Platform call ID, for logging.")


class AvailabilityResponse(BaseModel):
    available: bool
    date: dt.date
    slots: list[dt.datetime] = Field(description="Start times of all free slots on that day.")
    message: str = Field(description="Sentence the voice agent can speak to the caller.")


class BookingRequest(BaseModel):
    """Body of POST /tools/book-appointment."""

    model_config = ConfigDict(extra="ignore")

    customer_name: str = Field(min_length=1, max_length=100)
    customer_phone: str = Field(pattern=E164_PATTERN)
    start_time: dt.datetime = Field(description="Slot start, ISO 8601. Naive values mean UTC.")
    call_id: str | None = Field(
        default=None, description="Platform call ID, used to link the booking to the call."
    )


class BookingResponse(BaseModel):
    appointment_id: str
    start_time: dt.datetime
    end_time: dt.datetime
    message: str
