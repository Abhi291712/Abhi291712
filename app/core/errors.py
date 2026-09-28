"""Application exceptions and the handlers that turn them into consistent JSON errors.

Every error response has the same shape::

    {"error": {"code": "NOT_FOUND", "message": "Call abc was not found."}}

Services raise the exceptions defined here without knowing anything about HTTP; the handlers
registered in ``register_exception_handlers`` translate them into status codes. Responses on
``/tools`` endpoints also carry a top-level ``message`` so a voice agent can always speak
something back to the caller, even when a request fails.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

TOOLS_PREFIX = "/tools"
# Spoken fallback used by tool endpoints when the underlying error text is too technical.
TOOL_FALLBACK_MESSAGE = "Sorry, I'm having trouble with that request right now."


class AppError(Exception):
    """Base class for expected, well-understood errors raised by the application."""

    status_code = 500
    code = "INTERNAL_ERROR"
    default_message = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.code = code or self.code
        self.headers = headers
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    default_message = "The requested resource was not found."


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    default_message = "The request conflicts with the current state of the resource."


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"
    default_message = "Missing or invalid credentials."


class UnprocessableError(AppError):
    """Well-formed request that violates a business rule (e.g. booking outside opening hours)."""

    status_code = 422
    code = "UNPROCESSABLE"
    default_message = "The request could not be processed."


def error_response(
    request_path: str,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
    details: list[dict] | None = None,
) -> JSONResponse:
    """Build the standard error body. Shared by exception handlers and middleware."""
    error: dict = {"code": code, "message": message}
    if details:
        error["details"] = details
    body: dict = {"error": error}
    if request_path.startswith(TOOLS_PREFIX):
        # Voice agents read this field aloud, so it must exist on every tool response.
        body["message"] = message if status_code < 500 else TOOL_FALLBACK_MESSAGE
    return JSONResponse(status_code=status_code, content=body, headers=headers)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return error_response(request.url.path, exc.status_code, exc.code, exc.message, exc.headers)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Flatten Pydantic's error list into simple, serialisable {field, message} pairs.
    details = [
        {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
        for err in exc.errors()
    ]
    if request.url.path.startswith(TOOLS_PREFIX):
        message = "Sorry, I'm missing some details for that request. Could you repeat them?"
    else:
        message = "Request validation failed."
    return error_response(request.url.path, 422, "VALIDATION_ERROR", message, details=details)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # Covers framework-level errors such as unknown routes (404) or wrong methods (405).
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
    return error_response(
        request.url.path, exc.status_code, code, str(exc.detail), getattr(exc, "headers", None)
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Log the full traceback server-side but never leak internals to the client.
    logger.exception("Unhandled error while processing %s %s", request.method, request.url.path)
    return error_response(request.url.path, 500, "INTERNAL_ERROR", AppError.default_message)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all handlers to the app so every error leaves in the same format."""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
