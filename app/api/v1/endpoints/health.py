"""
app.api.v1.endpoints.health
───────────────────────────
Public liveness/readiness. No auth — safe for uptime pings.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_pool

log = get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    db_ok = False
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception as exc:
        log.warning("Health check DB probe failed: %s", exc)
    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "env": get_settings().app_env,
    }
