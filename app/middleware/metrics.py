"""Middleware that records request metrics and enforces the voice tool latency budget.

Every request is counted and timed. Agent tool endpoints get extra scrutiny: a voice agent is
in a live conversation while it waits, and silence of more than about a second feels broken to
a caller. Responses slower than ``TOOL_LATENCY_BUDGET_MS`` are logged as warnings and counted,
so slow tools show up on a dashboard (and can trigger an alert) before callers complain.
"""

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import metrics

logger = logging.getLogger(__name__)

TOOL_PREFIXES = ("/tools/", "/retell/functions/")


class MetricsMiddleware:
    def __init__(self, app: ASGIApp, tool_latency_budget_ms: int) -> None:
        self.app = app
        self.budget_seconds = tool_latency_budget_ms / 1000

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500

        async def send_and_measure(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body" and not message.get("more_body"):
                # Measure at the last byte sent, not after background tasks finish.
                self._record(scope, status_code, time.perf_counter() - started)
            await send(message)

        await self.app(scope, receive, send_and_measure)

    def _record(self, scope: Scope, status_code: int, elapsed: float) -> None:
        # The router stores the matched route on the scope; its path is the template
        # (/calls/{call_id}), which keeps the number of distinct label values small.
        route = getattr(scope.get("route"), "path", None) or "unmatched"
        method = scope["method"]
        metrics.HTTP_REQUESTS.labels(method, route, str(status_code)).inc()
        metrics.HTTP_LATENCY.labels(method, route).observe(elapsed)

        if scope["path"].startswith(TOOL_PREFIXES) and elapsed > self.budget_seconds:
            metrics.TOOL_LATENCY_BUDGET_EXCEEDED.labels(route).inc()
            logger.warning(
                "Tool response exceeded latency budget",
                extra={
                    "route": route,
                    "elapsed_ms": round(elapsed * 1000, 1),
                    "budget_ms": self.budget_seconds * 1000,
                },
            )
