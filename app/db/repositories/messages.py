"""
app.db.repositories.messages
────────────────────────────
All SQL for the `messages` table (call transcript rows).
"""
from __future__ import annotations

from app.db.session import get_pool


class MessagesRepository:
    async def insert(self, call_sid: str, role: str, content: str) -> None:
        """Insert a transcript row, tolerating the case where the parent
        `calls` row hasn't been persisted yet (we simply skip)."""
        if role not in ("customer", "ai"):
            raise ValueError(f"Invalid message role: {role!r}")

        pool = get_pool()
        async with pool.acquire() as conn:
            call_id = await conn.fetchval(
                "SELECT id FROM calls WHERE sid = $1", call_sid
            )
            if call_id is None:
                # Parent call row not yet written — skip silently.
                # This only happens on a brief race window; the in-memory
                # transcript still captures everything.
                return
            await conn.execute(
                """
                INSERT INTO messages (call_id, call_sid, role, content)
                VALUES ($1, $2, $3, $4)
                """,
                call_id, call_sid, role, content,
            )

    async def list_for_call(self, call_sid: str) -> list[dict]:
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT role, content, created_at FROM messages
                WHERE call_sid = $1
                ORDER BY created_at ASC
                """,
                call_sid,
            )
            return [dict(r) for r in rows]
