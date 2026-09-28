"""Authentication for API clients (API keys) and for voice platforms (signed requests).

Three trust mechanisms are needed:

* API clients and voice agent tools send an ``X-API-Key`` header that must match a configured key.
* The generic webhook endpoint expects ``X-Webhook-Timestamp`` plus ``X-Signature``, an
  HMAC-SHA256 over ``"<timestamp>." + raw body`` using a shared secret. Recomputing it proves
  the request came from the platform and was not altered. Including the timestamp in the signed
  data, and rejecting old timestamps, stops an attacker from replaying a captured request later.
* Retell AI signs its webhooks and function calls with an ``x-retell-signature`` header of the
  form ``v=<timestamp_ms>,d=<hex digest>``, where the digest is HMAC-SHA256 of
  ``raw body + timestamp`` keyed with the Retell API key.

All comparisons use ``hmac.compare_digest``, which takes the same time whether the first or the
last character differs, so an attacker cannot guess a secret by measuring response times.
"""

import hashlib
import hmac
import re
import time

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from app.core.config import Settings
from app.core.errors import UnauthorizedError

API_KEY_HEADER = "X-API-Key"
SIGNATURE_HEADER = "X-Signature"
TIMESTAMP_HEADER = "X-Webhook-Timestamp"
RETELL_SIGNATURE_HEADER = "x-retell-signature"

# Retell allows a five-minute clock difference between its servers and ours.
RETELL_TOLERANCE_MS = 5 * 60 * 1000
_RETELL_SIGNATURE = re.compile(r"^v=(\d+),d=([0-9a-fA-F]+)$")

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


# ---------- Generic webhook signatures ----------


def compute_signature(body: bytes, timestamp: str, secret: str) -> str:
    """Hex-encoded HMAC-SHA256 of "<timestamp>." followed by the raw request body."""
    signed_payload = timestamp.encode() + b"." + body
    return hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()


def is_valid_signature(
    body: bytes,
    signature: str | None,
    timestamp: str | None,
    secret: str,
    tolerance_seconds: int,
    now: float | None = None,
) -> bool:
    """Check signature and freshness. An optional "sha256=" prefix on the signature is accepted."""
    if not signature or not timestamp or not timestamp.isdigit():
        return False
    current = time.time() if now is None else now
    if abs(current - int(timestamp)) > tolerance_seconds:
        return False  # Too old (a replay) or too far in the future (a forged clock).
    provided = signature.removeprefix("sha256=").strip()
    return hmac.compare_digest(provided, compute_signature(body, timestamp, secret))


async def verified_webhook_body(
    request: Request, settings: Settings = Depends(get_app_settings)
) -> bytes:
    """FastAPI dependency: returns the raw body only if its signature is valid and fresh.

    The signature must be computed over the exact bytes received. Parsing JSON first and
    re-serialising it could change whitespace or key order and break verification.
    """
    body = await request.body()
    valid = is_valid_signature(
        body,
        request.headers.get(SIGNATURE_HEADER),
        request.headers.get(TIMESTAMP_HEADER),
        settings.webhook_secret,
        settings.webhook_tolerance_seconds,
    )
    if not valid:
        raise UnauthorizedError("Invalid or expired webhook signature.", code="INVALID_SIGNATURE")
    return body


# ---------- Retell AI signatures ----------


def compute_retell_signature(body: bytes, timestamp_ms: int, api_key: str) -> str:
    """Build an x-retell-signature header value (used by tests and local tooling)."""
    digest = hmac.new(api_key.encode(), body + str(timestamp_ms).encode(), hashlib.sha256)
    return f"v={timestamp_ms},d={digest.hexdigest()}"


def is_valid_retell_signature(
    body: bytes, header: str | None, api_key: str, now_ms: int | None = None
) -> bool:
    """Verify an x-retell-signature header the same way Retell's SDK `verify()` helper does."""
    if not header:
        return False
    match = _RETELL_SIGNATURE.match(header.strip())
    if not match:
        return False
    timestamp_ms, provided = int(match.group(1)), match.group(2)
    current_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if abs(current_ms - timestamp_ms) > RETELL_TOLERANCE_MS:
        return False
    expected = hmac.new(api_key.encode(), body + str(timestamp_ms).encode(), hashlib.sha256)
    return hmac.compare_digest(provided.lower(), expected.hexdigest())


async def verified_retell_body(
    request: Request, settings: Settings = Depends(get_app_settings)
) -> bytes:
    """FastAPI dependency for Retell endpoints: raw body if the Retell signature checks out."""
    if not settings.retell_api_key:
        # Refuse rather than accept unsigned traffic when the integration is not configured.
        raise UnauthorizedError("Retell integration is not configured.", code="RETELL_DISABLED")
    body = await request.body()
    header = request.headers.get(RETELL_SIGNATURE_HEADER)
    if not is_valid_retell_signature(body, header, settings.retell_api_key):
        raise UnauthorizedError("Invalid Retell signature.", code="INVALID_SIGNATURE")
    return body
