"""Middleware that assigns every request an ID and logs one access line per request.

The ID is taken from an incoming ``X-Request-ID`` header (so a caller or load balancer can
correlate its own logs with ours) or generated when absent. It is stored in a context variable
that the logging filter reads, and echoed back in the ``X-Request-ID`` response header.

This is written as plain ASGI middleware rather than Starlette's ``BaseHTTPMiddleware``, which
is faster and keeps background tasks and context variables working as expected.
"""

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_ctx

logger = logging.getLogger("app.access")

REQUEST_ID_HEADER = "X-Request-ID"
# Only accept short, simple IDs from clients; anything else could be used to forge log lines.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # Lifespan and websocket messages pass straight through.
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        # Each request runs in its own asyncio task with its own copy of the context, so this
        # value is visible to everything handling this request and to nothing else.
        request_id_ctx.set(request_id)

        started = time.perf_counter()
        status_code = 500  # Assumed until the app actually starts a response.

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            elif message["type"] == "http.response.body" and not message.get("more_body"):
                # Log when the response is complete, before any background tasks run, so the
                # duration reflects what the client actually experienced.
                logger.info(
                    "%s %s -> %s (%.1f ms)",
                    scope["method"],
                    scope["path"],
                    status_code,
                    (time.perf_counter() - started) * 1000,
                )
            await send(message)

        await self.app(scope, receive, send_with_request_id)
