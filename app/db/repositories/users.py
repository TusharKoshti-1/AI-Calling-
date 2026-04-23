"""
app.db.repositories.users
─────────────────────────
CRUD for the `users` table.
"""
from __future__ import annotations

from typing import Any

from app.db.session import get_pool


class UsersRepository:
    async def count(self) -> int:
        pool = get_pool()
        async with pool.acquire() as conn:
            result = await conn.fetchval("SELECT COUNT(*) FROM users")
            return int(result or 0)

    async def create(
        self,
        *,
        email: str,
        password_hash: str,
        full_name: str | None = None,
        is_admin: bool = False,
    ) -> dict[str, Any]:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO users (email, password_hash, full_name, is_admin)
                VALUES (LOWER($1), $2, $3, $4)
                RETURNING id, email, full_name, is_active, is_admin, created_at
                """,
                email, password_hash, full_name, is_admin,
            )
            return dict(row) if row else {}

    async def get_by_email(self, email: str) -> dict[str, Any] | None:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, email, password_hash, full_name, is_active, is_admin,
                       created_at, last_login_at
                FROM users
                WHERE LOWER(email) = LOWER($1)
                """,
                email,
            )
            return dict(row) if row else None

    async def get_by_id(self, user_id: str) -> dict[str, Any] | None:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, email, full_name, is_active, is_admin,
                       created_at, last_login_at
                FROM users
                WHERE id = $1
                """,
                user_id,
            )
            return dict(row) if row else None

    async def touch_login(self, user_id: str) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET last_login_at = NOW() WHERE id = $1",
                user_id,
            )
