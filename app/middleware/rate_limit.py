"""Per-API-key rate limiting using the token bucket algorithm.

Picture each API key owning a bucket that holds up to ``capacity`` tokens. Every request takes
one token; tokens drip back in at ``refill_rate`` per second. A full bucket allows a short burst,
while the refill rate sets the sustained limit. An empty bucket means ``429 Too Many Requests``
with a ``Retry-After`` header telling the client exactly when a token will be available.

Only requests carrying a *valid* key are counted. Requests with a missing or unknown key are
passed through so the auth dependency can reject them with 401; this also prevents an attacker
from filling memory with buckets for random keys.

Buckets live in process memory, which is correct for a single instance. Running several
instances would require a shared store such as Redis (see README "Future improvements").
"""

import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.errors import error_response
from app.core.security import is_valid_api_key


@dataclass
class TokenBucket:
    tokens: float
    updated_at: float


class RateLimiter:
    """Holds one token bucket per key. `clock` is injectable so tests can control time."""

    def __init__(
        self,
        capacity: int,
        refill_rate: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._clock = clock  # monotonic() never jumps backwards, unlike wall-clock time.
        self._buckets: dict[str, TokenBucket] = {}

    def acquire(self, key: str) -> tuple[bool, float]:
        """Try to take one token. Returns (allowed, seconds_until_next_token).

        This method has no `await`, so on the single-threaded event loop it runs atomically
        and no lock is needed.
        """
        now = self._clock()
        bucket = self._buckets.setdefault(key, TokenBucket(float(self.capacity), now))

        # Refill lazily: add the tokens that accumulated since the bucket was last touched.
        elapsed = now - bucket.updated_at
        bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_rate)
        bucket.updated_at = now

        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return True, 0.0
        return False, (1 - bucket.tokens) / self.refill_rate


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        limiter: RateLimiter,
        api_keys: frozenset[str],
        protected_prefixes: Iterable[str] = ("/calls", "/tools"),
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.api_keys = api_keys
        self.protected_prefixes = tuple(protected_prefixes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.protected_prefixes):
            await self.app(scope, receive, send)
            return

        api_key = dict(scope["headers"]).get(b"x-api-key", b"").decode("latin-1")
        if is_valid_api_key(api_key, self.api_keys):
            allowed, wait_seconds = self.limiter.acquire(api_key)
            if not allowed:
                retry_after = max(1, math.ceil(wait_seconds))  # Retry-After must be whole seconds.
                unit = "second" if retry_after == 1 else "seconds"
                response = error_response(
                    scope["path"],
                    429,
                    "RATE_LIMITED",
                    f"Too many requests right now. Please try again in {retry_after} {unit}.",
                    headers={"Retry-After": str(retry_after)},
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
