"""Authentication for API clients (API keys) and for the voice platform (webhook signatures).

Two different trust mechanisms are needed:

* API clients and voice agent tools send an ``X-API-Key`` header that must match a configured key.
* The voice platform signs each webhook body with HMAC-SHA256 using a shared secret. Recomputing
  the signature over the *raw* bytes proves the request came from the platform and was not
  altered in transit.

All comparisons use ``hmac.compare_digest``, which takes the same time whether the first or the
last character differs, so an attacker cannot guess a secret by measuring response times.
"""

import hashlib
import hmac

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from app.core.config import Settings
from app.core.errors import UnauthorizedError

API_KEY_HEADER = "X-API-Key"
SIGNATURE_HEADER = "X-Signature"

# auto_error=False lets us raise our own UnauthorizedError (consistent JSON) instead of
# FastAPI's default 403. Declaring the scheme also adds an "Authorize" button to /docs.
api_key_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


def get_app_settings(request: Request) -> Settings:
    """FastAPI dependency: the Settings instance the running app was created with."""
    return request.app.state.settings


def is_valid_api_key(candidate: str | None, valid_keys: frozenset[str]) -> bool:
    """Constant-time check of a candidate key against every configured key."""
    if not candidate:
        return False
    # Compare against all keys (no early exit) so timing does not reveal which key matched.
    matches = [hmac.compare_digest(candidate.encode(), key.encode()) for key in valid_keys]
    return any(matches)


def require_api_key(
    api_key: str | None = Depends(api_key_scheme),
    settings: Settings = Depends(get_app_settings),
) -> str:
    """FastAPI dependency protecting a route: returns the key or raises 401."""
    if not is_valid_api_key(api_key, settings.api_key_set):
        raise UnauthorizedError("Missing or invalid API key.")
    return api_key  # type: ignore[return-value]  # Non-None once validated.


def compute_signature(body: bytes, secret: str) -> str:
    """Hex-encoded HMAC-SHA256 of the raw request body."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def is_valid_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """Check a signature header value; an optional "sha256=" prefix is accepted."""
    if not signature:
        return False
    provided = signature.removeprefix("sha256=").strip()
    return hmac.compare_digest(provided, compute_signature(body, secret))


async def verified_webhook_body(
    request: Request, settings: Settings = Depends(get_app_settings)
) -> bytes:
    """FastAPI dependency: returns the raw body only if its HMAC signature is valid.

    The signature must be computed over the exact bytes received. Parsing JSON first and
    re-serialising it could change whitespace or key order and break verification.
    """
    body = await request.body()
    if not is_valid_signature(body, request.headers.get(SIGNATURE_HEADER), settings.webhook_secret):
        raise UnauthorizedError("Invalid webhook signature.", code="INVALID_SIGNATURE")
    return body
