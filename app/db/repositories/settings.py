"""
app.db.repositories.settings
────────────────────────────
Per-user key/value settings. A `NULL` user_id denotes a global default
(used as a fallback for users who haven't set the key yet).
"""
from __future__ import annotations

from app.db.session import get_pool


class SettingsRepository:
    async def get_all_for_user(self, user_id: str) -> dict[str, str]:
        """Return the effective settings dict for a user — their own keys
        take precedence over global defaults."""
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (key) key, value
                FROM settings
                WHERE user_id = $1 OR user_id IS NULL
                ORDER BY key,
                    CASE WHEN user_id IS NULL THEN 1 ELSE 0 END
                """,
                user_id,
            )
            return {r["key"]: r["value"] for r in rows}

    async def set_for_user(self, user_id: str, key: str, value: str) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            # Look for an existing user-specific row first.
            existing_id = await conn.fetchval(
                "SELECT id FROM settings WHERE user_id = $1 AND key = $2",
                user_id, key,
            )
            if existing_id:
                await conn.execute(
                    "UPDATE settings SET value = $1, updated_at = NOW() WHERE id = $2",
                    value, existing_id,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO settings (user_id, key, value)
                    VALUES ($1, $2, $3)
                    """,
                    user_id, key, value,
                )

    async def set_many_for_user(self, user_id: str, items: dict[str, str]) -> None:
        if not items:
            return
        pool = get_pool()
        async with pool.acquire() as conn, conn.transaction():
            for k, v in items.items():
                existing_id = await conn.fetchval(
                    "SELECT id FROM settings WHERE user_id = $1 AND key = $2",
                    user_id, k,
                )
                if existing_id:
                    await conn.execute(
                        "UPDATE settings SET value = $1, updated_at = NOW() WHERE id = $2",
                        v, existing_id,
                    )
                else:
                    await conn.execute(
                        "INSERT INTO settings (user_id, key, value) VALUES ($1, $2, $3)",
                        user_id, k, v,
                    )

    async def get_global(self, key: str, default: str = "") -> str:
        """Read a system-wide global default (user_id IS NULL)."""
        pool = get_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT value FROM settings WHERE user_id IS NULL AND key = $1",
                key,
            )
            return val if val is not None else default
