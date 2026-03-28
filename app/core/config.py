"""
app/core/config.py
Central configuration — all settings loaded from environment variables.
Add new settings here. Never import os.getenv anywhere else.
"""
from pydantic_settings import BaseSettings
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    # ── Server ────────────────────────────────────────────────
    APP_NAME: str = "CallSara"
    APP_VERSION: str = "1.0.0"
    PORT: int = 8000
    BASE_URL: str = "https://your-app.onrender.com"
    DEBUG: bool = False
    ENVIRONMENT: str = "production"  # local | staging | production

    # ── Telephony — Twilio ─────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = "ACc42f63df6f65d6b16d630cf74ea20bb5"
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM: str = "+16672206986"

    # ── Telephony — Telnyx (alternative) ──────────────────────
    TELNYX_API_KEY: str = ""
    TELNYX_FROM: str = ""
    TELNYX_APP_ID: str = ""

    # ── Active telephony provider ──────────────────────────────
    TELEPHONY_PROVIDER: str = "twilio"  # twilio | telnyx

    # ── AI — Groq ─────────────────────────────────────────────
    GROQ_API_KEY: str = "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"
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_TEMPERATURE: float = 0.3
    GROQ_MAX_TOKENS: int = 100

    # ── TTS — Cartesia ────────────────────────────────────────
    CARTESIA_API_KEY: str = "sk_car_LBXevqbfri3vbRtFc7w1xA"
    CARTESIA_VOICE_ID: str = "95d51f79-c397-46f9-b49a-23763d3eaa2d"
    CARTESIA_MODEL: str = "sonic-turbo"
    CARTESIA_VERSION: str = "2024-06-10"

    # ── Supabase ──────────────────────────────────────────────
    SUPABASE_URL: str = "https://zwiiinbjdnjgmclfknrq.supabase.co"
    SUPABASE_KEY: str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp3aWlpbmJqZG5qZ21jbGZrbnJxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MzU1OTA3NCwiZXhwIjoyMDg5MTM1MDc0fQ.HKYljZFeXUieNDhv2qCQ1_rvlZuXUgERx6TLlPDiUyo"
    SUPABASE_DB_HOST: str = ""           # set in env: pooler host from Supabase dashboard
    SUPABASE_DB_PORT: int = 6543         # transaction pooler port (works on Render)
    SUPABASE_DB_NAME: str = "postgres"
    SUPABASE_DB_USER: str = "postgres.zwiiinbjdnjgmclfknrq"
    SUPABASE_DB_PASS: str = ""           # set in env
    SUPABASE_BUCKET: str = "recordings"

    # ── Default agent config ───────────────────────────────────
    DEFAULT_AGENT_NAME: str = "Sara"
    DEFAULT_AGENCY_NAME: str = "Prestige Properties Dubai"
    DEFAULT_LANGUAGE: str = "en"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Cached settings singleton — import this everywhere."""
    return Settings()


# Convenience alias
settings = get_settings()
