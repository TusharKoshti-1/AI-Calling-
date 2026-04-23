"""
app.api.v1.endpoints.status
───────────────────────────
Returns the current user's status. Requires login so each tenant sees
only their own agent/agency/voice.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.core.security import AuthUser, get_current_user
from app.services.settings_service import settings_service

router = APIRouter(tags=["status"])


@router.get("/api/status")
async def api_status(
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> dict:
    s = get_settings()
    us = await settings_service.for_user(user.id)
    return {
        "agent":             us.get("agent_name", s.agent_name),
        "agency":            us.get("agency_name", s.agency_name),
        "twilio_configured": bool(s.twilio_auth_token),
        "from_number":       s.twilio_from,
        "base_url":          s.base_url,
        "env":               s.app_env,
        "user": {
            "id":       user.id,
            "email":    user.email,
            "name":     user.full_name,
            "is_admin": user.is_admin,
        },
    }
