"""
app.db.repositories.settings
────────────────────────────
Key/value application settings stored in the `settings` table.
"""
from __future__ import annotations

from app.db.session import get_pool


class SettingsRepository:
    async def get(self, key: str, default: str = "") -> str:
        pool = get_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT value FROM settings WHERE key = $1", key
            )
            return val if val is not None else default

    async def set(self, key: str, value: str) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO settings (key, value) VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()
                """,
                key, value,
            )

    async def get_all(self) -> dict[str, str]:
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT key, value FROM settings")
            return {r["key"]: r["value"] for r in rows}

    async def set_many(self, items: dict[str, str]) -> None:
        if not items:
            return
        pool = get_pool()
        async with pool.acquire() as conn, conn.transaction():
            for k, v in items.items():
                await conn.execute(
                    """
                    INSERT INTO settings (key, value) VALUES ($1, $2)
                    ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()
                    """,
                    k, v,
                )
