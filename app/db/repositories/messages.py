"""
app.db.repositories.messages
────────────────────────────
User-scoped CRUD on the `messages` table.
"""
from __future__ import annotations

from app.db.session import get_pool


class MessagesRepository:
    async def insert(
        self, *, call_sid: str, user_id: str, role: str, content: str
    ) -> None:
        if role not in ("customer", "ai"):
            raise ValueError(f"Invalid message role: {role!r}")

        pool = get_pool()
        async with pool.acquire() as conn:
            # Resolve the parent call row. If the call belongs to a different
            # user we refuse — defence in depth in case a SID is forged.
            row = await conn.fetchrow(
                "SELECT id, user_id FROM calls WHERE sid = $1", call_sid
            )
            if row is None:
                # Parent call not yet written (brief race) — skip silently.
                return
            if row["user_id"] is not None and str(row["user_id"]) != str(user_id):
                # SID owned by someone else — refuse.
                return

            await conn.execute(
                """
                INSERT INTO messages (call_id, call_sid, user_id, role, content)
                VALUES ($1, $2, $3, $4, $5)
                """,
                row["id"], call_sid, user_id, role, content,
            )

    async def list_for_call(self, *, call_sid: str, user_id: str) -> list[dict]:
        """List only if the call belongs to this user (otherwise returns [])."""
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT m.role, m.content, m.created_at
                FROM messages m
                JOIN calls c ON c.sid = m.call_sid
                WHERE m.call_sid = $1 AND c.user_id = $2
                ORDER BY m.created_at ASC
                """,
                call_sid, user_id,
            )
            return [dict(r) for r in rows]
