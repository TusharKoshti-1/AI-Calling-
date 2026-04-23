"""
app.api.v1.endpoints.status
───────────────────────────
Non-sensitive configuration summary consumed by the dashboard sidebar.
Unauthenticated by design — it only reveals info already exposed in the
dashboard header (agent name, whether Twilio is reachable).
"""
from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.services.settings_service import settings_service

router = APIRouter(tags=["status"])


@router.get("/api/status")
async def api_status() -> dict:
    s = get_settings()
    return {
        "agent":             settings_service.get("agent_name", s.agent_name),
        "agency":            settings_service.get("agency_name", s.agency_name),
        "twilio_configured": bool(s.twilio_auth_token),
        "from_number":       s.twilio_from,
        "base_url":          s.base_url,
        "env":               s.app_env,
    }
