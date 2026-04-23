"""
app.api.v1.endpoints.settings
─────────────────────────────
Dashboard-facing runtime configuration. Protected by the admin API key.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.logging import get_logger
from app.core.security import require_admin_api_key
from app.schemas.settings import SettingsUpdate
from app.services.settings_service import settings_service

log = get_logger(__name__)

router = APIRouter(
    tags=["settings"],
    dependencies=[Depends(require_admin_api_key)],
)


@router.get("/api/settings")
async def api_get_settings() -> dict:
    # Re-pull from DB on demand so the dashboard always sees fresh values
    # even if someone tweaked the DB directly.
    try:
        await settings_service.load()
    except Exception as exc:
        log.warning("Settings refresh from DB failed: %s", exc)
    return settings_service.public_snapshot()


@router.post("/api/settings")
async def api_save_settings(body: SettingsUpdate) -> dict:
    saved = await settings_service.update(
        body.model_dump(exclude_none=True, exclude_unset=True)
    )
    # Never echo back the OpenAI key.
    echoed = {k: v for k, v in saved.items() if k != "openai_api_key"}
    if "openai_api_key" in saved:
        echoed["openai_api_key_saved"] = True
    return {"success": True, "saved": echoed}
