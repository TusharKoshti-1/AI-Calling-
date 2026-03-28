"""
db/repositories/settings.py
All DB queries for the settings table.
"""
from db.database import get_pool
from app.core.logging import get_logger

log = get_logger(__name__)


async def get_setting(key: str, default: str = "") -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        val = await conn.fetchval("SELECT value FROM settings WHERE key=$1", key)
        return val if val is not None else default


async def set_setting(key: str, value: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO settings (key, value)
            VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value=$2, updated_at=NOW()
        """, key, value)


async def get_all_settings() -> dict:
    """Return all settings as a key→value dict."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT key, value, label, description FROM settings ORDER BY key"
        )
        return {r["key"]: r["value"] for r in rows}


async def get_settings_with_meta() -> list[dict]:
    """Return settings with label and description for the Settings UI."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT key, value, label, description FROM settings ORDER BY key"
        )
        return [dict(r) for r in rows]
