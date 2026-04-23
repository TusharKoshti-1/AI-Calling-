"""
app.schemas.settings
────────────────────
Request/response models for settings + voice preview endpoints.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class SettingsUpdate(BaseModel):
    """Partial update payload. Unknown fields are ignored server-side."""

    agent_name: str | None = None
    agency_name: str | None = None
    system_prompt: str | None = None
    voice_id: str | None = None
    llm_provider: str | None = Field(default=None, pattern="^(groq|openai)$")
    openai_api_key: str | None = None
    openai_model: str | None = None
    groq_model: str | None = None


class VoicePreviewRequest(BaseModel):
    voice_id: str = Field(..., min_length=8)
    text: str | None = None
