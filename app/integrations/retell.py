"""Translation layer between Retell AI's request formats and this service's own schemas.

Retell AI (https://www.retellai.com) runs the voice agent: it answers the phone, talks to the
caller and calls our backend in two situations:

* **Webhooks** (``call_started``, ``call_ended``, ``call_analyzed``) describing the call. Retell's
  payload looks like ``{"event": "call_ended", "call": {"call_id": ..., "start_timestamp": ...}}``
  with timestamps in epoch milliseconds and no per-delivery event ID.
* **Custom functions** the agent's LLM decides to call mid-conversation. The request body is
  ``{"name": "...", "call": {...}, "args": {...}}`` (or just the args, if the function is
  configured with "args only").

Everything Retell-specific is contained here. The rest of the application only ever sees its
own ``WebhookEvent`` / ``AvailabilityRequest`` / ``BookingRequest`` models, so supporting another
voice platform later means writing another adapter, not changing the business logic.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.schemas.webhook import WebhookCallData, WebhookEvent

# Retell events this service acts on. Others (e.g. transcript updates) are acknowledged and ignored.
SUPPORTED_EVENTS = frozenset({"call_started", "call_ended", "call_analyzed"})


class RetellCallAnalysis(BaseModel):
    model_config = ConfigDict(extra="allow")  # Retell adds fields over time; keep them all.

    call_summary: str | None = None
    user_sentiment: str | None = None


class RetellCall(BaseModel):
    model_config = ConfigDict(extra="allow")

    call_id: str
    agent_id: str | None = None
    from_number: str | None = None
    to_number: str | None = None
    start_timestamp: int | None = None  # Epoch milliseconds.
    end_timestamp: int | None = None
    transcript: str | None = None
    disconnection_reason: str | None = None
    call_analysis: RetellCallAnalysis | None = None


class RetellWebhook(BaseModel):
    model_config = ConfigDict(extra="allow")

    event: str
    call: RetellCall


class RetellFunctionCall(BaseModel):
    """Body of a Retell custom function request (the full form, not "args only")."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    call: RetellCall | None = None
    args: dict[str, Any] = {}


def _from_epoch_ms(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value / 1000, tz=UTC) if value else None


def to_webhook_event(webhook: RetellWebhook) -> WebhookEvent:
    """Convert a Retell webhook into the service's generic WebhookEvent."""
    call = webhook.call
    analysis = call.call_analysis
    return WebhookEvent(
        # Retell sends each event type once per call, so call ID + event type identifies a
        # delivery. Retries of the same delivery therefore deduplicate correctly.
        event_id=f"retell:{call.call_id}:{webhook.event}",
        event_type=webhook.event,  # Validated against EventType by the WebhookEvent model.
        call=WebhookCallData(
            call_id=call.call_id,
            agent_id=call.agent_id,
            from_number=call.from_number,
            to_number=call.to_number,
            started_at=_from_epoch_ms(call.start_timestamp),
            ended_at=_from_epoch_ms(call.end_timestamp),
            transcript=call.transcript,
            summary=analysis.call_summary if analysis else None,
            # Retell reports "Positive"/"Negative"/"Neutral"/"Unknown"; store lower-case.
            sentiment=analysis.user_sentiment.lower()
            if analysis and analysis.user_sentiment
            else None,
        ),
    )


def extract_function_args(body: dict[str, Any]) -> dict[str, Any]:
    """Return the tool arguments, adding the Retell call ID so bookings link to the call.

    Handles both the full payload (``{"call": ..., "args": ...}``) and "args only" mode.
    """
    if "args" not in body and "call" not in body:
        return dict(body)  # "Args only" mode: the body *is* the arguments.
    request = RetellFunctionCall.model_validate(body)
    args = dict(request.args)
    if request.call and "call_id" not in args:
        args["call_id"] = request.call.call_id
    return args
