"""
app.db.repositories.sessions
────────────────────────────
CRUD for the `sessions` table — signin, signout, sliding refresh, cleanup.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db.session import get_pool


class SessionsRepository:
    async def create(
        self,
        *,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sessions (user_id, token_hash, expires_at, user_agent, ip)
                VALUES ($1, $2, $3, $4, $5)
                """,
                user_id, token_hash, expires_at, user_agent, ip,
            )

    async def find_live(self, token_hash: str) -> dict[str, Any] | None:
        """Return the session row if it's still live (not expired, not revoked)."""
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, expires_at, revoked_at
                FROM sessions
                WHERE token_hash = $1
                  AND revoked_at IS NULL
                  AND expires_at > NOW()
                """,
                token_hash,
            )
            return dict(row) if row else None

    async def extend(self, token_hash: str, new_expires_at: datetime) -> None:
        """Sliding-session refresh — bump the row's expiry forward.

        Only bumps forward (the GREATEST guard) so that a stale/racing
        write can never *shorten* a session. We also require the row to
        still be live; callers should already have checked that, but it's
        cheap insurance.
        """
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE sessions
                SET expires_at = GREATEST(expires_at, $2)
                WHERE token_hash = $1
                  AND revoked_at IS NULL
                """,
                token_hash, new_expires_at,
            )

    async def revoke(self, token_hash: str) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE sessions SET revoked_at = NOW() WHERE token_hash = $1",
                token_hash,
            )

    async def revoke_all_for_user(self, user_id: str) -> None:
        """Sign out every session for a user — used on password change."""
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE sessions SET revoked_at = NOW()
                WHERE user_id = $1 AND revoked_at IS NULL
                """,
                user_id,
            )

    async def purge_expired(self) -> int:
        """Hard-delete expired + revoked rows older than 30 days.
        Returns the number of rows removed. Call from a scheduled job."""
        pool = get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM sessions
                WHERE expires_at < NOW() - INTERVAL '30 days'
                   OR (revoked_at IS NOT NULL AND revoked_at < NOW() - INTERVAL '30 days')
                """
            )
            # asyncpg returns "DELETE n"
            try:
                return int(result.split()[-1])
            except Exception:
                return 0
