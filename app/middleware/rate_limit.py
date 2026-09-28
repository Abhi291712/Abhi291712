"""Per-API-key rate limiting using the token bucket algorithm, in memory or in Redis.

Picture each API key owning a bucket that holds up to ``capacity`` tokens. Every request takes
one token; tokens drip back in at ``refill_rate`` per second. A full bucket allows a short burst,
while the refill rate sets the sustained limit. An empty bucket means ``429 Too Many Requests``
with a ``Retry-After`` header telling the client exactly when a token will be available.

Two interchangeable backends:

* ``RateLimiter`` keeps buckets in process memory: zero setup, correct for a single instance.
* ``RedisRateLimiter`` keeps buckets in Redis, so every instance behind a load balancer shares
  the same limits. The refill-and-take step runs as a Lua script, which Redis executes
  atomically, so two instances can never both spend the last token.

Only requests carrying a *valid* key are counted. Requests with a missing or unknown key are
passed through so the auth dependency can reject them with 401; this also prevents an attacker
from filling memory (or Redis) with buckets for random keys.
"""

import hashlib
import logging
import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core import metrics
from app.core.errors import error_response
from app.core.security import is_valid_api_key

logger = logging.getLogger(__name__)


class Limiter(Protocol):
    async def acquire(self, key: str) -> tuple[bool, float]:
        """Try to take one token. Returns (allowed, seconds_until_next_token)."""
        ...


@dataclass
class TokenBucket:
    tokens: float
    updated_at: float


class RateLimiter:
    """In-memory buckets. `clock` is injectable so tests can control time."""

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

    async def acquire(self, key: str) -> tuple[bool, float]:
        # There is no `await` inside, so on the single-threaded event loop this runs
        # atomically and no lock is needed.
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


# The same algorithm as RateLimiter.acquire, executed inside Redis. Returns {allowed, wait}.
# `wait` is returned as a string because Redis truncates Lua numbers to integers.
TOKEN_BUCKET_LUA = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1]) or capacity
local ts = tonumber(state[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local allowed = 0
local wait = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  wait = (1 - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
-- Idle buckets expire once they would be full again, so Redis does not grow forever.
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / rate) + 1)
return {allowed, tostring(wait)}
"""


class RedisRateLimiter:
    """Buckets shared by all instances through Redis.

    Uses the app servers' wall-clock time, so their clocks should be NTP-synchronised (the
    default on every cloud platform). If Redis is unreachable the limiter fails open: an outage
    of a protective component should not take the whole API down with it.
    """

    def __init__(
        self,
        redis: Any,
        capacity: int,
        refill_rate: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._clock = clock
        self._script = redis.register_script(TOKEN_BUCKET_LUA)

    @staticmethod
    def _redis_key(api_key: str) -> str:
        # Store a hash, never the API key itself.
        return "ratelimit:" + hashlib.sha256(api_key.encode()).hexdigest()[:32]

    async def acquire(self, key: str) -> tuple[bool, float]:
        try:
            allowed, wait = await self._script(
                keys=[self._redis_key(key)],
                args=[self.capacity, self.refill_rate, self._clock()],
            )
        except Exception:
            logger.warning("Redis rate limiter unavailable; allowing request", exc_info=True)
            return True, 0.0
        return bool(int(allowed)), float(wait)


def build_limiter(capacity: int, refill_rate: float, redis_url: str | None) -> Limiter:
    """Redis-backed when REDIS_URL is set, in-memory otherwise."""
    if not redis_url:
        return RateLimiter(capacity, refill_rate)
    import redis.asyncio as redis_asyncio  # Only needed when Redis is configured.

    return RedisRateLimiter(redis_asyncio.from_url(redis_url), capacity, refill_rate)


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        limiter: Limiter,
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
            allowed, wait_seconds = await self.limiter.acquire(api_key)
            if not allowed:
                metrics.RATE_LIMITED.inc()
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
