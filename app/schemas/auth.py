"""
app.schemas.auth
────────────────
Pydantic models for the auth endpoints.
"""
from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=200)
    full_name: str | None = Field(default=None, max_length=200)


class SigninRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=200)


class AuthUserOut(BaseModel):
    id: str
    email: str
    full_name: str | None = None
    is_admin: bool = False


class AuthResponse(BaseModel):
    user: AuthUserOut
