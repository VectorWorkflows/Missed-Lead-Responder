# app/config.py
"""
Central configuration.

Everything the app needs comes from environment variables (loaded from .env
locally, injected by docker-compose in production). Nothing is hardcoded, and
nothing silently defaults to a value that would make the phone misbehave.
"""

from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ------------------------------------------------------------------ core
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    # The https URL Twilio uses to reach this app, e.g. https://api.example.com
    # NO trailing slash (we strip it defensively below - a trailing slash here
    # produces "//webhook/..." which breaks BOTH routing and signature checks).
    PUBLIC_BASE_URL: str

    # --------------------------------------------------------------- twilio
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str

    # When a number belongs to a Messaging Service, outbound SMS should be sent
    # through the service (MG...) rather than the raw number. Leave blank to
    # send from the number directly.
    TWILIO_MESSAGING_SERVICE_SID: str = ""

    # Leave True in production. Only flip to False for a few minutes while
    # debugging a reverse-proxy problem - it disables webhook authentication.
    TWILIO_VALIDATE_SIGNATURE: bool = True

    # ----------------------------------------------------------------- data
    MONGO_URI: str
    MONGO_DB_NAME: str = "missed_call_responder"

    # Durable local state (outbox queue + client config snapshot).
    # MUST be a mounted docker volume, otherwise queued work is lost on deploy.
    DATA_DIR: str = "/data"

    # --------------------------------------------------------- integrations
    # Base64-encoded service account JSON, or raw JSON, or a path to a file.
    # Optional: if unset, Sheets sync is skipped and everything else still runs.
    GOOGLE_SERVICE_ACCOUNT_JSON: str = ""
    TELEGRAM_BOT_TOKEN: str

    # Where system/ops alerts go (bot crashes, dead queue items, heartbeats).
    # Defaults to FALLBACK_TELEGRAM_CHAT_ID if unset.
    OPS_TELEGRAM_CHAT_ID: Optional[int] = None

    # ------------------------------------------------- last-resort safety net
    # If the database is unreachable AND the on-disk snapshot is missing, the
    # IVR falls back to these values so a caller ALWAYS hears a working menu.
    # Set these to your own business. This is the difference between "sounds
    # completely normal" and "this number is currently unavailable".
    FALLBACK_ENABLED: bool = True
    FALLBACK_CLIENT_ID: str = "fallback"
    FALLBACK_BUSINESS_NAME: str = "our team"
    FALLBACK_TWILIO_NUMBER: str = ""
    FALLBACK_BOOKING_URL: str = ""
    FALLBACK_INTAKE_URL: str = ""
    FALLBACK_TELEGRAM_CHAT_ID: Optional[int] = None
    FALLBACK_SHEET_ID: str = ""
    FALLBACK_TIMEZONE: str = "Asia/Kolkata"
    FALLBACK_WEBSITE_URL: str = ""

    # THE HONESTY SWITCH.
    # False  -> the IVR never promises a text. Used when the number cannot send
    #           SMS (A2P/toll-free registration incomplete). Callers are offered
    #           a voicemail, a callback, and the website instead.
    # True   -> full "press 2 / press 3 and we'll text you the link" menu.
    # Per-client `sms_enabled` in client_configs overrides this.
    FALLBACK_SMS_ENABLED: bool = False

    # If True, an unrecognised inbound number still gets the fallback IVR
    # instead of a rejection. Keeps you safe from a mistyped config.
    FALLBACK_FOR_UNKNOWN_NUMBERS: bool = True

    # ------------------------------------------------------------ behaviour
    CLIENT_CACHE_REFRESH_SECONDS: int = 60
    OUTBOX_POLL_SECONDS: int = 5
    OUTBOX_MAX_ATTEMPTS: int = 14          # ~ retries out to about 24h
    OUTBOX_RETENTION_DAYS: int = 30

    SMS_LOOKBACK_DAYS: int = 14            # how far back to match a reply
    IVR_GATHER_TIMEOUT: int = 7
    VOICEMAIL_MAX_SECONDS: int = 120

    ENABLE_SCHEDULER: bool = True
    # Follow-up: wait at least this long, THEN wait for the client's local
    # morning. A 10pm call gets nudged at 9am, not at midnight.
    FOLLOWUP_AFTER_HOURS: int = 2
    FOLLOWUP_HOUR_LOCAL: int = 9
    FOLLOWUP_MAX_PER_LEAD: int = 1
    OWNER_REMINDER_AFTER_HOURS: int = 4    # nudge YOU about an untouched lead
    HEARTBEAT_HOUR_LOCAL: int = 9          # daily "still alive" Telegram ping
    ENABLE_HEARTBEAT: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ----------------------------------------------------------- validators
    @field_validator("PUBLIC_BASE_URL")
    @classmethod
    def _clean_base_url(cls, v: str) -> str:
        v = (v or "").strip().rstrip("/")
        if not v:
            raise ValueError("PUBLIC_BASE_URL must be set")
        if not v.startswith(("http://", "https://")):
            raise ValueError("PUBLIC_BASE_URL must start with http:// or https://")
        return v

    @field_validator("FALLBACK_TWILIO_NUMBER", "FALLBACK_BOOKING_URL", "FALLBACK_INTAKE_URL")
    @classmethod
    def _strip(cls, v: str) -> str:
        return (v or "").strip()

    @property
    def ops_chat_id(self) -> Optional[int]:
        return self.OPS_TELEGRAM_CHAT_ID or self.FALLBACK_TELEGRAM_CHAT_ID


settings = Settings()
