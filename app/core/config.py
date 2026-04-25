"""
app.core.config
───────────────
Typed application settings loaded from environment / .env.
All secrets are read from the environment — never hardcode in source.
"""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_session_secret() -> str:
    """
    Resolve the session secret in a way that is safe across:

      • Multiple uvicorn workers (Dockerfile runs `--workers 2`), which each
        import this module in their own process. If the secret were
        generated per-process, worker A would mint a cookie that worker B
        could not validate — the user would appear "logged in" to the API
        yet get bounced back to /signin on the very next request.
      • Render redeploys / container restarts. If the secret rotated every
        boot, every active session would die on every deploy. Unacceptable
        for a SaaS.

    Resolution order:
      1. `SESSION_SECRET` env var — authoritative. Set this in production
         to a long random string and keep it stable across deploys.
      2. In non-production environments, a persistent file at
         `/tmp/.callsara_session_secret` (or `$CALLSARA_SECRET_FILE`) so
         every worker in the same box reads the same value, and restarts
         survive as long as /tmp is intact.
      3. In production, fail loudly instead of silently issuing cookies
         that no other worker / next deploy can validate.
    """
    from_env = os.environ.get("SESSION_SECRET", "").strip()
    if from_env:
        return from_env

    # Production MUST set SESSION_SECRET — don't silently fall back.
    if os.environ.get("APP_ENV", "").strip().lower() == "production":
        raise RuntimeError(
            "SESSION_SECRET is not set. In production you must set a long, "
            "random, stable SESSION_SECRET environment variable — otherwise "
            "every worker / redeploy invalidates every user's session."
        )

    # Dev fallback: persist to a file so all workers share and restarts survive.
    secret_path = Path(
        os.environ.get("CALLSARA_SECRET_FILE", "/tmp/.callsara_session_secret")
    )
    try:
        if secret_path.exists():
            existing = secret_path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        value = secrets.token_urlsafe(48)
        secret_path.write_text(value, encoding="utf-8")
        try:
            os.chmod(secret_path, 0o600)
        except Exception:
            pass
        return value
    except Exception:
        # Unwritable filesystem — last resort. Will break multi-worker in dev
        # but at least a single worker will work.
        return secrets.token_urlsafe(48)


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

    # ── Session / cookie auth ────────────────────────────────
    # Opaque session tokens are signed with this secret. Resolved by
    # _resolve_session_secret() to guarantee stability across workers
    # and restarts — see that function for the full rationale.
    session_secret: str = Field(
        default_factory=_resolve_session_secret,
        description="HMAC secret for session tokens. Must be set in prod.",
    )

    # 30 days — long enough that users on a SaaS aren't constantly
    # logging back in. We slide the expiry forward on every authenticated
    # request, so active users effectively never expire.
    session_ttl_hours: int = 24 * 30

    # When a session has less than this many hours remaining, any
    # authenticated request refreshes its expiry back to the full TTL
    # and reissues the cookie with a new Max-Age. This is the "sliding
    # session" pattern — keeps active users signed in indefinitely
    # while inactive sessions still expire.
    session_refresh_within_hours: int = 24 * 7  # refresh if <7 days left

    session_cookie_name: str = "callsara_session"
    # In production we default to Secure cookies. You can still override
    # this via env for local HTTPS testing behind ngrok, etc.
    session_cookie_secure: bool | None = None  # resolved via property below
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
    # gpt-4o is the production default — noticeably more natural and
    # robust to off-script moments than gpt-4o-mini, which is the
    # biggest lever for call quality. Users can still override per-user
    # in the Settings page.
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    # Higher temperature lets Sara sound conversational rather than
    # reading scripted lines verbatim. 0.3 was fine for a rigid state
    # machine; 0.7 is better for a persona.
    openai_temperature: float = 0.7
    # 120 enforces brevity: at ~3-4 tokens per word, this allows up to
    # ~30 words = roughly 2 short sentences, which is the prompt's hard
    # ceiling. v9 used 180; with v11's stronger "ONE sentence preferred"
    # prompt, 120 is plenty and slightly faster (less to generate, less
    # to TTS, lower cost).
    openai_max_tokens: int = 120

    llm_provider: Literal["groq", "openai"] = "openai"

    # ── Cartesia ──────────────────────────────────────────────
    cartesia_api_key: str = ""
    cartesia_voice_id: str = "95d51f79-c397-46f9-b49a-23763d3eaa2d"
    cartesia_model: str = "sonic-turbo"
    cartesia_version: str = "2024-11-13"

    # ── ElevenLabs ────────────────────────────────────────────
    # Optional second TTS provider. Selected per-user via voice_id:
    # 20-char no-dash IDs route to ElevenLabs, 36-char UUIDs to Cartesia.
    # Leave api_key blank if you only want to use Cartesia — calls with
    # an ElevenLabs voice ID will then fail gracefully (logged error,
    # falls back to "Sorry, I missed that" recovery line). The model
    # default `eleven_flash_v2_5` is their phone-call-optimised model
    # (~250 ms latency vs ~700 ms for the multilingual_v2 quality model).
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "UgBBYS2sOqTuMpoF3BR0"  # warm female, en-US default
    elevenlabs_model: str = "eleven_flash_v2_5"

    # ── Supabase ──────────────────────────────────────────────
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = "recordings"

    supabase_db_host: str = ""
    supabase_db_port: int = 6543
    supabase_db_name: str = "postgres"
    supabase_db_user: str = ""
    supabase_db_pass: str = ""
    supabase_db_pool_min: int = 1
    supabase_db_pool_max: int = 10

    # ── Agent defaults (seed for new users only) ─────────────
    # These are shown as placeholders / used when a tenant hasn't
    # configured their own values yet. agent_name defaults to "Sara"
    # because it's a neutral English name that works across verticals;
    # agency_name is intentionally empty — the prompt template falls
    # back to "our team" if the tenant hasn't set one yet.
    agent_name: str = "Sara"
    agency_name: str = ""

    # ── Signup policy ────────────────────────────────────────
    # If false, only the first user can self-register; subsequent users
    # must be invited (future feature).
    allow_public_signup: bool = True

    # ── Computed helpers ─────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def effective_cookie_secure(self) -> bool:
        """Whether to set the `Secure` flag on the session cookie.

        If explicitly configured via env (SESSION_COOKIE_SECURE=true/false),
        respect that. Otherwise default to True in production (Render / any
        HTTPS deploy) and False in development so cookies still work over
        plain http://localhost.
        """
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return self.is_production

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
