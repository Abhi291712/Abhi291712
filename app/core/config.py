"""Application configuration loaded from environment variables and an optional .env file.

Every tunable value (database URL, API keys, secrets, rate limits, business hours, integrations)
lives in a single typed ``Settings`` object. Keeping configuration in one place means the code
never reads ``os.environ`` directly, values are validated at startup, and tests can build their
own ``Settings`` instance without touching the real environment.

Optional integrations (Retell, Claude, Redis, Google Calendar, tracing) are switched on simply by
providing their settings; with the defaults the service runs fully offline.
"""

from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings. Each field maps to an upper-case environment variable (e.g. API_KEYS)."""

    # Read values from a local .env file if present; unknown variables are ignored.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "voice-ai-backend"
    environment: str = "development"
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"

    # SQLite works out of the box; any SQLAlchemy URL (e.g. Postgres) can be supplied instead.
    database_url: str = "sqlite:///./voice_ai_backend.db"
    # Convenient for local development and tests. Production runs `alembic upgrade head`
    # instead and sets this to false so the schema is only ever changed by migrations.
    auto_create_tables: bool = True

    # Stored as a comma-separated string because that is the most natural format in a .env file.
    api_keys: str = "dev-key-1"
    webhook_secret: str = "change-me-webhook-secret"
    # Signed webhooks older (or newer) than this are rejected, which blocks replayed requests.
    webhook_tolerance_seconds: int = Field(default=300, ge=1)

    # Token bucket: a client may burst up to `capacity` requests, then gets `refill` per second.
    rate_limit_capacity: int = Field(default=60, ge=1)
    rate_limit_refill_per_second: float = Field(default=1.0, gt=0)
    # When set, buckets live in Redis so every instance of the service shares the same limits.
    redis_url: str | None = None

    # Appointment scheduling rules used by the voice agent tools, in the business's local time.
    business_timezone: str = "UTC"
    business_open_hour: int = Field(default=9, ge=0, le=23)
    business_close_hour: int = Field(default=17, ge=1, le=24)
    appointment_slot_minutes: int = Field(default=30, ge=5, le=240)
    # Tool responses slower than this are logged and counted: callers are waiting on the line.
    tool_latency_budget_ms: int = Field(default=800, ge=1)

    # Durable webhook processing: a background sweeper retries events that were stored but not
    # processed (e.g. the server restarted) or that failed. 0 disables the sweeper.
    event_retry_interval_seconds: float = Field(default=30.0, ge=0)
    event_retry_min_age_seconds: float = Field(default=30.0, ge=0)
    event_max_attempts: int = Field(default=5, ge=1)

    # Retell AI: the account API key is also the secret Retell signs its requests with.
    retell_api_key: str | None = None

    # LLM call analysis with Claude. The API key may also come from ANTHROPIC_API_KEY or an
    # `ant auth login` profile, so the feature is switched on explicitly.
    llm_analysis_enabled: bool = False
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5"

    # Google Calendar sync (optional): a service account with access to the calendar.
    google_calendar_id: str | None = None
    google_service_account_file: str | None = None

    # Observability.
    metrics_enabled: bool = True
    # Exporter details come from the standard OTEL_* environment variables.
    tracing_enabled: bool = False

    # Outbound client for the voice platform's REST API.
    voice_platform_base_url: str = "https://api.example-voice-platform.com"
    voice_platform_api_key: str = "replace-me"
    voice_platform_timeout_seconds: float = 10.0
    voice_platform_max_retries: int = Field(default=3, ge=0)

    @model_validator(mode="after")
    def check_settings(self) -> "Settings":
        # Fail at startup rather than producing an empty schedule or a crash at runtime.
        if self.business_open_hour >= self.business_close_hour:
            raise ValueError("BUSINESS_OPEN_HOUR must be earlier than BUSINESS_CLOSE_HOUR")
        try:
            ZoneInfo(self.business_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown BUSINESS_TIMEZONE '{self.business_timezone}'") from exc
        return self

    @property
    def api_key_set(self) -> frozenset[str]:
        """Parsed API keys; blank entries are dropped so a trailing comma is harmless."""
        return frozenset(key.strip() for key in self.api_keys.split(",") if key.strip())

    @property
    def tz(self) -> ZoneInfo:
        """The business time zone as a tzinfo object (ZoneInfo caches instances itself)."""
        return ZoneInfo(self.business_timezone)

    @property
    def calendar_enabled(self) -> bool:
        return bool(self.google_calendar_id and self.google_service_account_file)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance so the environment is parsed only once per process."""
    return Settings()
