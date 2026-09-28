"""Structured logging with a request ID attached to every log line.

A ``ContextVar`` holds the ID of the request currently being handled. A custom log-record
factory copies that value onto every log record the moment it is created, so any
``logger.info(...)`` call anywhere in the code base (or in a library) is automatically tagged
with the request that caused it, no matter which handler eventually writes it out. This makes
it possible to follow a single request through the logs even when many are interleaved.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

# Holds the current request ID. "-" is used for log lines emitted outside of a request.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

# Attributes present on every LogRecord; anything else was passed via `extra=` and is logged.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "request_id"}


# The factory Python would use by default; ours wraps it and adds one attribute.
_default_record_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs) -> logging.LogRecord:
    """Create a LogRecord exactly as usual, then stamp it with the current request ID."""
    record = _default_record_factory(*args, **kwargs)
    record.request_id = request_id_ctx.get()
    return record


class JsonFormatter(logging.Formatter):
    """Renders log records as single-line JSON, which log aggregators can index."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        # Include structured fields supplied with logger.info("...", extra={...}).
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Configure the root logger once at startup."""
    handler = logging.StreamHandler(sys.stdout)
    handler._app_handler = True  # type: ignore[attr-defined]  # Marks handlers we own.
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
        )

    # Safe to call repeatedly: it always wraps the original factory, never itself.
    logging.setLogRecordFactory(_record_factory)

    root = logging.getLogger()
    # Replace only our own handler, so calling this twice (e.g. in tests) never duplicates
    # log lines and handlers added by other tools (such as pytest) are left alone.
    for existing in [h for h in root.handlers if getattr(h, "_app_handler", False)]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Send uvicorn's own messages through the same handler so they share the same format.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # RequestIdMiddleware already writes one access line per request (with the request ID),
    # so uvicorn's access log would only produce duplicates without it.
    logging.getLogger("uvicorn.access").disabled = True
