"""
app/api/v1/endpoints/status.py
System status — health check and app info for dashboard.
"""
from fastapi import APIRouter
from app.core.config import settings
from app.core.logging import get_logger
from app.services import settings_service

log = get_logger(__name__)
router = APIRouter(prefix="/status", tags=["status"])


@router.get("")
async def get_status():
    """GET /api/v1/status — app config and connection status."""
    db_ok = False
    try:
        from db.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        pass

    return {
        "app":               settings.APP_NAME,
        "version":           settings.APP_VERSION,
        "environment":       settings.ENVIRONMENT,
        "agent_name":        settings_service.get_agent_name(),
        "agency_name":       settings_service.get_agency_name(),
        "from_number":       settings.TWILIO_FROM,
        "provider":          settings.TELEPHONY_PROVIDER,
        "twilio_configured": bool(settings.TWILIO_AUTH_TOKEN),
        "db_connected":      db_ok,
        "base_url":          settings.BASE_URL,
    }
