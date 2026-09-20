"""
app/config.py
─────────────
Centralised configuration for the Forsa News Agent.
All values are read from environment variables / a .env file.
No secrets are hard-coded here.
"""
from __future__ import annotations

from typing import Literal
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All application configuration in one place.
    Values are loaded from the environment (or .env file).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        ...,
        description="PostgreSQL asyncpg connection URL",
    )

    # ── Google Gemini (LLM) ───────────────────────────────────────────────────
    google_api_key: str = Field(..., description="Google AI API key for Gemini")
    gemini_classify_model: str = Field(
        default="gemini-flash-latest",
        description="Gemini model used for cheap classification",
    )
    gemini_summarize_model: str = Field(
        default="gemini-flash-latest",
        description="Gemini model used for quality summarization",
    )

    # ── SerpAPI & Search Fallback ─────────────────────────────────────────────
    serpapi_key: str = Field(..., description="SerpAPI key for Google News searches")
    force_search_fallback: bool = Field(
        default=False,
        description="Force fallback search even if SerpAPI has quota (useful for testing)",
    )
    serpapi_min_searches: int = Field(
        default=5,
        description="Minimum searches required to attempt SerpAPI; below this switches to fallback",
    )

    # ── Email Delivery ────────────────────────────────────────────────────────
    email_provider: Literal["smtp", "microsoft365", "power_automate"] = Field(
        default="smtp",
        description="Email delivery provider: 'smtp', 'microsoft365' (Graph API), or 'power_automate' (HTTP Webhook Flow)",
    )

    # Power Automate Flow HTTP trigger URL (used if email_provider='power_automate')
    power_automate_webhook_url: str = Field(
        default="",
        description="Power Automate 'When an HTTP request is received' trigger URL",
    )

    # SMTP configuration (used if email_provider='smtp')
    smtp_host: str = Field(default="", description="SMTP server hostname")
    smtp_port: int = Field(default=587, description="SMTP server port")
    smtp_user: str = Field(default="", description="SMTP authentication username")
    smtp_pass: str = Field(default="", description="SMTP authentication password")
    smtp_use_tls: bool = Field(default=True, description="Use STARTTLS for SMTP")

    # Microsoft 365 Graph API configuration (used if email_provider='microsoft365')
    microsoft_tenant_id: str = Field(
        default="",
        description="Azure AD / Microsoft Entra ID Tenant ID",
    )
    microsoft_client_id: str = Field(
        default="",
        description="Azure App Registration Application (client) ID",
    )
    microsoft_client_secret: str = Field(
        default="",
        description="Azure App Registration Client Secret value",
    )

    email_recipient: str = Field(..., description="CEO email address")
    email_from: str = Field(
        default="Forsa News Agent <newsagent@forsaegypt.com>",
        description="Sender name and address shown in email From header",
    )
    email_subject_prefix: str = Field(
        default="Forsa Financial Market News",
        description="Email subject line prefix",
    )

    # ── Scheduling ────────────────────────────────────────────────────────────
    schedule_frequency: Literal["daily", "hourly", "twice_daily", "weekly", "custom_cron"] = Field(
        default="weekly",
        description="How often the pipeline runs",
    )
    schedule_time: str = Field(
        default="08:30",
        description="HH:MM local time for daily / weekly / twice_daily first run",
    )
    schedule_time_2: str = Field(
        default="20:00",
        description="HH:MM local time for twice_daily second run",
    )
    schedule_day_of_week: str = Field(
        default="sun",
        description="Day of week for weekly schedule (mon, tue, wed, thu, fri, sat, sun)",
    )
    schedule_cron: str = Field(
        default="0 8 * * *",
        description="Cron expression used when schedule_frequency=custom_cron",
    )
    timezone: str = Field(
        default="Africa/Cairo",
        description="Timezone for scheduling and date display",
    )

    # ── Processing Window ─────────────────────────────────────────────────────
    overlap_hours: int = Field(
        default=6,
        description="Hours of overlap before last-run time to catch delayed articles",
    )
    initial_lookback_hours: int = Field(
        default=168,
        description="How far back to look on the very first/test run (in hours, 168 = 7 days)",
    )

    # ── Filtering Thresholds ──────────────────────────────────────────────────
    importance_threshold: int = Field(
        default=60,
        ge=0,
        le=100,
        description="Minimum importance score (0-100) for an event to be emailed",
    )
    relevance_threshold: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Minimum relevance score (0-100) for an article to proceed",
    )
    similarity_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Cosine similarity threshold for semantic deduplication",
    )
    title_similarity_threshold: int = Field(
        default=85,
        ge=0,
        le=100,
        description="Fuzzy title match threshold (0-100) for title deduplication",
    )

    # ── No-News Behaviour ─────────────────────────────────────────────────────
    no_news_behaviour: Literal["send_empty", "skip"] = Field(
        default="skip",
        description="What to do when no important news found: send_empty or skip",
    )

    # ── Retry & Resilience ────────────────────────────────────────────────────
    http_retry_attempts: int = Field(default=3, ge=1)
    http_timeout_seconds: int = Field(default=30, ge=5)
    email_retry_attempts: int = Field(default=3, ge=1)

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")

    # ── Environment ───────────────────────────────────────────────────────────
    environment: Literal["development", "production", "test"] = Field(
        default="production"
    )

    @field_validator("email_subject_prefix", mode="before")
    @classmethod
    def strip_email_subject_prefix(cls, v: str) -> str:
        return v.strip() if isinstance(v, str) else v

    @field_validator("schedule_time", "schedule_time_2")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError(f"Time must be HH:MM format, got: {v!r}")
        hh, mm = parts
        if not hh.isdigit() or not mm.isdigit():
            raise ValueError(f"Time must be HH:MM format, got: {v!r}")
        if not (0 <= int(hh) <= 23) or not (0 <= int(mm) <= 59):
            raise ValueError(f"Time out of range: {v!r}")
        return v

    @property
    def schedule_hour(self) -> int:
        return int(self.schedule_time.split(":")[0])

    @property
    def schedule_minute(self) -> int:
        return int(self.schedule_time.split(":")[1])

    @property
    def schedule_hour_2(self) -> int:
        return int(self.schedule_time_2.split(":")[0])

    @property
    def schedule_minute_2(self) -> int:
        return int(self.schedule_time_2.split(":")[1])


# ── Module-level singleton ────────────────────────────────────────────────────
# Import this object anywhere in the codebase:  from app.config import settings
settings = Settings()  # pyright: ignore[reportCallIssue]
