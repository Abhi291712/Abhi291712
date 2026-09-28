"""Webhook pipeline: store each event once, then apply it to the call record in the background.

The work is split into two phases on purpose:

1. ``record_event`` runs inside the HTTP request. It only stores the event (deduplicating by
   ``event_id``) so the service can answer 200 within milliseconds. Voice platforms treat slow
   webhook responses as failures and retry them, which would create even more load.
2. ``process_event`` runs afterwards as a background task. It updates the call and never moves
   its status backwards, because webhooks can arrive out of order (for example ``call_ended``
   before ``call_started`` after a network retry).
"""

import logging
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core import metrics
from app.core.database import utcnow
from app.models.call import STATUS_RANK, Call, CallStatus
from app.models.event import Event, EventStatus, EventType
from app.repositories.call_repo import CallRepository
from app.repositories.event_repo import EventRepository
from app.schemas.webhook import WebhookEvent
from app.services.call_analysis import CallAnalyzer, analyze_call

logger = logging.getLogger(__name__)

# The status each event type implies. An analysis only exists for a finished call.
EVENT_TARGET_STATUS: dict[EventType, CallStatus] = {
    EventType.CALL_STARTED: CallStatus.ONGOING,
    EventType.CALL_ENDED: CallStatus.ENDED,
    EventType.CALL_ANALYZED: CallStatus.ENDED,
}

UNKNOWN_AGENT = "unknown"

# Events after which a transcript can exist, so LLM analysis is worth attempting.
ANALYSIS_TRIGGERS = frozenset({EventType.CALL_ENDED, EventType.CALL_ANALYZED})


class WebhookService:
    def __init__(self, session: Session, calls: CallRepository, events: EventRepository) -> None:
        self.session = session
        self.calls = calls
        self.events = events

    def record_event(self, event: WebhookEvent) -> bool:
        """Store the event. Returns False if this event_id was already stored (a duplicate)."""
        if self.events.get_by_event_id(event.event_id):
            logger.info("Duplicate webhook ignored", extra={"event_id": event.event_id})
            metrics.WEBHOOK_EVENTS.labels(event.event_type, "duplicate").inc()
            return False

        stored = Event(
            event_id=event.event_id,
            event_type=event.event_type,
            call_external_id=event.call.call_id,
            payload=event.model_dump(mode="json"),  # mode="json" makes datetimes serialisable.
        )
        try:
            self.events.add(stored)
            self.session.commit()
        except IntegrityError:
            # A concurrent delivery of the same event inserted it first.
            self.session.rollback()
            logger.info("Duplicate webhook ignored (race)", extra={"event_id": event.event_id})
            metrics.WEBHOOK_EVENTS.labels(event.event_type, "duplicate").inc()
            return False

        metrics.WEBHOOK_EVENTS.labels(event.event_type, "received").inc()
        logger.info(
            "Webhook received",
            extra={"event_id": event.event_id, "event_type": str(event.event_type)},
        )
        return True

    def process_event(self, event_id: str) -> None:
        """Apply a stored event to its call. Failures are recorded on the event, not raised."""
        stored = self.events.get_by_event_id(event_id)
        if stored is None or stored.status == EventStatus.PROCESSED:
            return

        event = WebhookEvent.model_validate(stored.payload)
        # Counted on every attempt so the retry sweeper can give up on an event that keeps
        # failing. It is assigned just before each commit because a rollback discards it.
        attempt = (stored.attempts or 0) + 1
        try:
            try:
                self._apply(event)
            except IntegrityError:
                # Another event for the same new call created the call row concurrently.
                # Roll back and retry once; this time the call will be found and updated.
                self.session.rollback()
                self._apply(event)
            stored.attempts = attempt
            self.events.mark_processed(stored)
            self.session.commit()
            metrics.WEBHOOK_EVENTS.labels(stored.event_type, "processed").inc()
            logger.info("Webhook processed", extra={"event_id": event_id})
        except Exception as exc:
            self.session.rollback()
            stored.attempts = attempt
            self.events.mark_failed(stored, repr(exc))
            self.session.commit()
            metrics.WEBHOOK_EVENTS.labels(stored.event_type, "failed").inc()
            logger.exception("Webhook processing failed", extra={"event_id": event_id})

    def maybe_analyze(self, event_id: str, analyzer: CallAnalyzer) -> None:
        """After a call-ending event, run LLM analysis once the transcript is available."""
        stored = self.events.get_by_event_id(event_id)
        if stored is None or stored.event_type not in ANALYSIS_TRIGGERS:
            return
        call = self.calls.get_by_external_id(stored.call_external_id)
        if call is None or call.analysis is not None or not call.transcript:
            return
        try:
            analyze_call(self.session, call, analyzer)
        except Exception:
            # The LLM is an enhancement: an outage must never break call processing.
            self.session.rollback()
            metrics.LLM_ANALYSES.labels("error").inc()
            logger.exception("Call analysis failed", extra={"call_id": call.id})

    def _apply(self, event: WebhookEvent) -> None:
        data = event.call
        call = self.calls.get_by_external_id(data.call_id)
        if call is None:
            # First time we hear about this call (it was not created through POST /calls).
            call = self.calls.add(
                Call(
                    external_call_id=data.call_id,
                    agent_id=data.agent_id or UNKNOWN_AGENT,
                    from_number=data.from_number,
                    to_number=data.to_number,
                    status=CallStatus.REGISTERED,
                )
            )

        # Status only moves forward. A late call_started after call_ended is still useful for
        # its timestamps, but it must not reopen a finished call.
        target = EVENT_TARGET_STATUS[event.event_type]
        current = CallStatus(call.status)
        if STATUS_RANK[target] > STATUS_RANK[current]:
            call.status = target
        elif target != current:
            logger.info(
                "Out-of-order event: status change ignored",
                extra={"event_id": event.event_id, "current": str(current), "event": str(target)},
            )

        # Fill in details without ever overwriting known values with missing ones.
        if call.agent_id == UNKNOWN_AGENT and data.agent_id:
            call.agent_id = data.agent_id
        call.from_number = call.from_number or data.from_number
        call.to_number = call.to_number or data.to_number

        started_at = data.started_at
        if started_at is None and event.event_type == EventType.CALL_STARTED:
            started_at = event.occurred_at  # Fall back to the event time for call_started.
        call.started_at = call.started_at or started_at
        call.ended_at = call.ended_at or data.ended_at

        # Analysis fields get richer over time, so newer non-empty values win.
        if data.transcript:
            call.transcript = data.transcript
        if data.summary:
            call.summary = data.summary
        if data.sentiment:
            call.sentiment = data.sentiment


def build_webhook_service(session: Session) -> WebhookService:
    return WebhookService(session, CallRepository(session), EventRepository(session))


def reprocess_pending_events(
    session_factory: sessionmaker[Session],
    *,
    min_age_seconds: float,
    max_attempts: int,
    analyzer: CallAnalyzer | None = None,
) -> int:
    """Retry events that were never processed or that failed. Returns how many were attempted.

    Called periodically by the sweeper started in `app.main`. Because every event is stored
    before it is processed, a crash or restart can delay processing but never lose an event.
    """
    cutoff = utcnow() - timedelta(seconds=min_age_seconds)
    with session_factory() as session:
        event_ids = [
            e.event_id
            for e in EventRepository(session).list_retryable(
                older_than=cutoff, max_attempts=max_attempts
            )
        ]
    for event_id in event_ids:
        # A fresh session per event, so one bad event cannot affect the others.
        process_event_in_background(session_factory, event_id, analyzer)
    if event_ids:
        logger.info("Retried pending webhook events", extra={"count": len(event_ids)})
    return len(event_ids)


def process_event_in_background(
    session_factory: sessionmaker[Session],
    event_id: str,
    analyzer: CallAnalyzer | None = None,
) -> None:
    """Entry point for FastAPI BackgroundTasks.

    Runs after the response has been sent, when the request's database session is already
    closed, so it opens (and closes) its own session. When LLM analysis is enabled, it also
    analyses the call once its transcript has arrived.
    """
    with session_factory() as session:
        service = build_webhook_service(session)
        service.process_event(event_id)
        if analyzer is not None:
            service.maybe_analyze(event_id, analyzer)
