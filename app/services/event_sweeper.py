"""Background loop that retries webhook events which were not processed successfully.

FastAPI's ``BackgroundTasks`` run inside the web process. If that process stops between storing
an event and processing it (a deploy, a crash, a scaling event), the event would otherwise sit
in the ``received`` state forever. This sweeper runs for the lifetime of the app and, every
``EVENT_RETRY_INTERVAL_SECONDS``, hands such events (and failed ones) back to the pipeline.

Because processing is idempotent (status only moves forward, fields are only filled in), it is
safe if an event is occasionally processed twice.
"""

import asyncio
import logging

from anyio import to_thread
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.services.webhook_service import reprocess_pending_events

logger = logging.getLogger(__name__)


async def run_event_sweeper(session_factory: sessionmaker[Session], settings: Settings) -> None:
    """Run until cancelled (the app's lifespan cancels it on shutdown)."""
    interval = settings.event_retry_interval_seconds
    logger.info("Event retry sweeper started", extra={"interval_seconds": interval})
    while True:
        await asyncio.sleep(interval)
        try:
            # Database work is blocking, so it runs in a worker thread to keep the event
            # loop free for incoming requests.
            await to_thread.run_sync(
                lambda: reprocess_pending_events(
                    session_factory,
                    min_age_seconds=settings.event_retry_min_age_seconds,
                    max_attempts=settings.event_max_attempts,
                )
            )
        except Exception:
            # Never let one bad sweep (e.g. the database briefly unavailable) stop the loop.
            logger.exception("Event retry sweep failed")
