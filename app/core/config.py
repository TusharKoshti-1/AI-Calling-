"""
app.core.config
───────────────
Typed application settings loaded from environment / .env.
All secrets are read from the environment — never hardcode in source.
"""
from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Server ────────────────────────────────────────────────
    app_env: Literal["development", "staging", "production"] = "development"
    port: int = 8000
    base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    cors_origins: str = "*"

    # ── Session / JWT signing ────────────────────────────────
    # Opaque session tokens are signed with this secret. In production
    # this MUST be set to a long random string. In dev we generate one
    # per process so restarts invalidate old cookies.
    session_secret: str = Field(
        default_factory=lambda: secrets.token_urlsafe(32),
        description="HMAC secret for session tokens. Must be set in prod.",
    )
    session_ttl_hours: int = 24 * 30
    session_cookie_name: str = "callsara_session"
    session_cookie_secure: bool = False   # set True behind HTTPS in prod
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # ── Twilio signature verification ────────────────────────
    verify_twilio_signature: bool = False

    # ── Twilio ────────────────────────────────────────────────
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from: str = ""

    # ── Groq ──────────────────────────────────────────────────
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_temperature: float = 0.3
    groq_max_tokens: int = 100

    # ── OpenAI ────────────────────────────────────────────────
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_temperature: float = 0.3
    openai_max_tokens: int = 100

    llm_provider: Literal["groq", "openai"] = "groq"

    # ── Cartesia ──────────────────────────────────────────────
    cartesia_api_key: str = ""
    cartesia_voice_id: str = "95d51f79-c397-46f9-b49a-23763d3eaa2d"
    cartesia_model: str = "sonic-turbo"
    cartesia_version: str = "2024-11-13"

    # ── Supabase ──────────────────────────────────────────────
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = "recordings"

    supabase_db_host: str = ""
    supabase_db_port: int = 6543
    supabase_db_name: str = "postgres"
    supabase_db_user: str = ""
    supabase_db_password: str = ""
    supabase_db_pool_min: int = 1
    supabase_db_pool_max: int = 10

    # ── Agent defaults (seed for new users) ──────────────────
    agent_name: str = "Sara"
    agency_name: str = "Prestige Properties Dubai"

    # ── Signup policy ────────────────────────────────────────
    # If false, only the first user can self-register; subsequent users
    # must be invited (future feature).
    allow_public_signup: bool = True

    # ── Computed helpers ─────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
