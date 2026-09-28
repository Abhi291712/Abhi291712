"""Application entry point: builds the FastAPI app and wires every component together.

``create_app`` is an application factory. Production code calls it with no arguments (settings
come from the environment), while tests call it with custom ``Settings`` pointing at a separate
test database. Nothing is created at import time except the default ``app`` object that uvicorn
serves via ``uvicorn app.main:app``.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.core.config import Settings, get_settings
from app.core.database import build_engine, build_session_factory, init_db
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.middleware.rate_limit import RateLimiter, RateLimitMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.routers import calls, health, tools, webhooks

logger = logging.getLogger(__name__)

DESCRIPTION = """
Backend service connecting voice AI agents (such as Retell AI) to business systems.

* **Calls**: create, list, fetch and update call records (`X-API-Key` required).
* **Webhooks**: signed call lifecycle events from the voice platform.
* **Agent tools**: availability and booking endpoints a voice agent calls mid-conversation.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    engine = build_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Startup: make sure tables exist. Shutdown: close pooled DB connections cleanly.
        init_db(engine)
        logger.info("Application started", extra={"environment": settings.environment})
        yield
        engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    # Shared objects live on app.state so dependencies can reach them via `request.app`.
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)

    register_exception_handlers(app)

    # Middleware added last runs first. RequestIdMiddleware is outermost so that even
    # responses produced by the rate limiter carry an X-Request-ID and are logged.
    app.add_middleware(
        RateLimitMiddleware,
        limiter=RateLimiter(settings.rate_limit_capacity, settings.rate_limit_refill_per_second),
        api_keys=settings.api_key_set,
    )
    app.add_middleware(RequestIdMiddleware)

    app.include_router(health.router)
    app.include_router(calls.router)
    app.include_router(webhooks.router)
    app.include_router(tools.router)
    return app


# The ASGI application served by uvicorn (`uvicorn app.main:app`).
app = create_app()
