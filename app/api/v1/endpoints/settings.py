"""
app/api/v1/endpoints/settings.py
REST API — read and update agent/call settings.
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.services import settings_service
from app.services.ai.llm import DEFAULT_SYSTEM_PROMPT
from db.repositories.settings import get_settings_with_meta

log = get_logger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])

ALLOWED_KEYS = {
    "agent.name",
    "agent.agency_name",
    "agent.language",
    "agent.intro_text",
    "agent.system_prompt",
    "call.speech_timeout",
    "call.record",
}


@router.get("")
async def get_settings():
    """GET /api/v1/settings — all settings with current values."""
    try:
        meta = await get_settings_with_meta()
        current = settings_service.get_all()
        result  = []
        for row in meta:
            result.append({
                "key":         row["key"],
                "value":       current.get(row["key"], row["value"]),
                "label":       row.get("label", ""),
                "description": row.get("description", ""),
            })
        # Also return resolved values for easy frontend use
        sp = settings_service.get_system_prompt()
        resolved_prompt = (
            DEFAULT_SYSTEM_PROMPT.format(
                agent_name=settings_service.get_agent_name(),
                agency_name=settings_service.get_agency_name(),
            )
            if sp in ("", "default")
            else sp
        )
        return {
            "settings":        result,
            "resolved": {
                "agent_name":    settings_service.get_agent_name(),
                "agency_name":   settings_service.get_agency_name(),
                "intro_text":    settings_service.get_intro_text(),
                "system_prompt": resolved_prompt,
            },
        }
    except Exception as e:
        log.error(f"get_settings error: {e}")
        return {"settings": [], "resolved": {}}


@router.post("")
async def save_settings(request_body: dict):
    """POST /api/v1/settings — save one or many settings."""
    updates = {}
    for key, value in request_body.items():
        if key in ALLOWED_KEYS and isinstance(value, str):
            updates[key] = value.strip()

    if not updates:
        return JSONResponse(
            {"success": False, "error": "No valid settings provided"},
            status_code=400,
        )

    await settings_service.save_many(updates)

    # Invalidate intro cache if identity settings changed
    identity_keys = {"agent.name", "agent.agency_name", "agent.intro_text"}
    if updates.keys() & identity_keys:
        settings_service.invalidate_intro_cache()

    log.info(f"Settings saved: {list(updates.keys())}")
    return {"success": True, "saved": list(updates.keys())}


@router.post("/reset-prompt")
async def reset_prompt():
    """POST /api/v1/settings/reset-prompt — restore default system prompt."""
    await settings_service.save("agent.system_prompt", "default")
    return {"success": True, "message": "System prompt reset to default"}
