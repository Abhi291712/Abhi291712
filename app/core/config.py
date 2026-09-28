"""Application configuration loaded from environment variables and an optional .env file.

Every tunable value (database URL, API keys, secrets, rate limits, business hours) lives in a
single typed ``Settings`` object. Keeping configuration in one place means the code never
reads ``os.environ`` directly, values are validated at startup, and tests can build their own
``Settings`` instance without touching the real environment.
"""

from functools import lru_cache
from typing import Literal

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

    # Stored as a comma-separated string because that is the most natural format in a .env file.
    api_keys: str = "dev-key-1"
    webhook_secret: str = "change-me-webhook-secret"

    # Token bucket: a client may burst up to `capacity` requests, then gets `refill` per second.
    rate_limit_capacity: int = Field(default=60, ge=1)
    rate_limit_refill_per_second: float = Field(default=1.0, gt=0)

    # Appointment scheduling rules used by the voice agent tools (UTC hours).
    business_open_hour: int = Field(default=9, ge=0, le=23)
    business_close_hour: int = Field(default=17, ge=1, le=24)
    appointment_slot_minutes: int = Field(default=30, ge=5, le=240)

    # Outbound client for the voice platform's REST API.
    voice_platform_base_url: str = "https://api.example-voice-platform.com"
    voice_platform_api_key: str = "replace-me"
    voice_platform_timeout_seconds: float = 10.0
    voice_platform_max_retries: int = Field(default=3, ge=0)

    @model_validator(mode="after")
    def check_business_hours(self) -> "Settings":
        # Fail at startup rather than producing an empty schedule at runtime.
        if self.business_open_hour >= self.business_close_hour:
            raise ValueError("BUSINESS_OPEN_HOUR must be earlier than BUSINESS_CLOSE_HOUR")
        return self

    @property
    def api_key_set(self) -> frozenset[str]:
        """Parsed API keys; blank entries are dropped so a trailing comma is harmless."""
        return frozenset(key.strip() for key in self.api_keys.split(",") if key.strip())


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance so the environment is parsed only once per process."""
    return Settings()
