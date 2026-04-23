"""
app.schemas.calls
─────────────────
Request/response models for call-related endpoints.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class DialRequest(BaseModel):
    phone: str = Field(..., min_length=5, max_length=20, description="E.164 phone number")


class DialResponse(BaseModel):
    success: bool
    sid: str | None = None
    error: str | None = None
