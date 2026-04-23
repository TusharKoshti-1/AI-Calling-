"""
app.api.v1.endpoints.settings
─────────────────────────────
Per-user settings — each tenant has their own agent_name, voice_id,
LLM provider, OpenAI key, and system prompt.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.logging import get_logger
from app.core.security import AuthUser, get_current_user
from app.schemas.settings import SettingsUpdate
from app.services.settings_service import settings_service

log = get_logger(__name__)
router = APIRouter(tags=["settings"])


@router.get("/api/settings")
async def api_get_settings(
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> dict:
    us = await settings_service.for_user(user.id)
    return us.public_snapshot()


@router.post("/api/settings")
async def api_save_settings(
    body: SettingsUpdate,
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> dict:
    saved = await settings_service.update_for_user(
        user.id,
        body.model_dump(exclude_none=True, exclude_unset=True),
    )
    echoed = {k: v for k, v in saved.items() if k != "openai_api_key"}
    if "openai_api_key" in saved:
        echoed["openai_api_key_saved"] = True
    return {"success": True, "saved": echoed}
