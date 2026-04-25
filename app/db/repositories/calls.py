"""
app.db.repositories.calls
─────────────────────────
User-scoped CRUD on the `calls` table. Every method takes `user_id` so
SaaS tenants can't see each other's calls.

Lookups by SID (from Twilio webhooks) are scoped via the SID's stored
`user_id` — once the outbound call is registered, subsequent webhook
events are matched to the right tenant automatically.
"""
from __future__ import annotations

from typing import Any

from app.db.session import get_pool


class CallsRepository:
    # ── Writes ────────────────────────────────────────────────
    async def upsert(self, sid: str, user_id: str, data: dict[str, Any]) -> str | None:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO calls (sid, user_id, phone, from_number, status, hot_lead,
                                   duration_sec, started_at, agent_name, agency_name)
                VALUES ($1,$2,$3,$4,$5,$6,$7,NOW(),$8,$9)
                ON CONFLICT (sid) DO UPDATE SET
                    status       = EXCLUDED.status,
                    hot_lead     = GREATEST(calls.hot_lead, EXCLUDED.hot_lead),
                    duration_sec = EXCLUDED.duration_sec,
                    updated_at   = NOW()
                RETURNING id
                """,
                sid, user_id,
                data.get("phone", ""),
                data.get("from_number", ""),
                data.get("status", "ringing"),
                data.get("hot_lead", False),
                data.get("duration_sec", 0),
                data.get("agent_name", "Sara"),
                data.get("agency_name", ""),
            )
            return str(row["id"]) if row else None

    async def update_by_sid(self, sid: str, **fields: Any) -> None:
        """Update by SID only (used from webhooks where we don't need the user_id
        because the SID itself identifies a unique call). Whitelisted fields."""
        if not fields:
            return
        allowed = {
            "status", "hot_lead", "duration_sec", "recording_url",
            "recording_path", "transcript", "phone", "from_number",
            "agent_name", "agency_name",
        }
        safe = {k: v for k, v in fields.items() if k in allowed}
        if not safe:
            return
        set_clauses = [f"{k} = ${i + 2}" for i, k in enumerate(safe)]
        sql = (
            f"UPDATE calls SET {', '.join(set_clauses)}, updated_at = NOW() "
            f"WHERE sid = $1"
        )
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(sql, sid, *safe.values())

    async def finalize_by_sid(
        self,
        sid: str,
        status: str,
        duration_sec: int,
        recording_url: str,
        recording_path: str,
        transcript: str,
    ) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE calls SET status=$2, duration_sec=$3, recording_url=$4,
                    recording_path=$5, transcript=$6, ended_at=NOW(), updated_at=NOW()
                WHERE sid=$1
                """,
                sid, status, duration_sec, recording_url, recording_path, transcript,
            )

    async def set_recording_by_sid(
        self, sid: str, recording_url: str, recording_path: str
    ) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE calls SET recording_url=$2, recording_path=$3, updated_at=NOW()
                WHERE sid=$1
                """,
                sid, recording_url, recording_path,
            )

    async def delete_by_id(
        self, call_id: str, user_id: str
    ) -> dict[str, str] | None:
        """Delete a call row, scoped to the owning user.

        Returns a dict with `sid` and `recording_path` of the deleted row
        so the caller can clean up the recording file in object storage,
        or None if no row matched (call doesn't exist OR belongs to a
        different user — same response either way to avoid leaking the
        existence of other tenants' calls).

        Tenant safety: the WHERE clause requires BOTH the id and user_id.
        If user A tries to delete user B's call by guessing the id, the
        DELETE just affects 0 rows and we return None.

        Cascade: messages.call_id has ON DELETE CASCADE in the schema, so
        the transcript rows go automatically — no second query needed.
        """
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                DELETE FROM calls
                WHERE id = $1::uuid AND user_id = $2::uuid
                RETURNING sid, COALESCE(recording_path, '') AS recording_path
                """,
                call_id, user_id,
            )
            return dict(row) if row else None

    # ── Reads — always user-scoped ────────────────────────────
    async def get_user_for_sid(self, sid: str) -> str | None:
        """Look up which user owns a given call SID (used by webhooks)."""
        pool = get_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT user_id::text FROM calls WHERE sid = $1", sid
            )
            return val

    async def list(
        self,
        *,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        hot_only: bool = False,
        search: str | None = None,
    ) -> list[dict]:
        conditions = ["user_id = $1"]
        values: list[Any] = [user_id]
        idx = 2
        if status and status != "all":
            conditions.append(f"status = ${idx}")
            values.append(status); idx += 1
        if hot_only:
            conditions.append("hot_lead = TRUE")
        if search:
            conditions.append(f"phone ILIKE ${idx}")
            values.append(f"%{search}%"); idx += 1

        where = "WHERE " + " AND ".join(conditions)
        values.extend([limit, offset])

        sql = f"""
            SELECT id, sid, phone, status, hot_lead, duration_sec,
                   started_at, ended_at, recording_url, transcript,
                   agent_name, agency_name
            FROM calls {where}
            ORDER BY started_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *values)
            return [dict(r) for r in rows]

    async def count(
        self,
        *,
        user_id: str,
        status: str | None = None,
        hot_only: bool = False,
        search: str | None = None,
    ) -> int:
        conditions = ["user_id = $1"]
        values: list[Any] = [user_id]
        idx = 2
        if status and status != "all":
            conditions.append(f"status = ${idx}")
            values.append(status); idx += 1
        if hot_only:
            conditions.append("hot_lead = TRUE")
        if search:
            conditions.append(f"phone ILIKE ${idx}")
            values.append(f"%{search}%"); idx += 1

        where = "WHERE " + " AND ".join(conditions)
        sql = f"SELECT COUNT(*) FROM calls {where}"
        pool = get_pool()
        async with pool.acquire() as conn:
            result = await conn.fetchval(sql, *values)
            return int(result or 0)

    async def stats(self, user_id: str) -> dict[str, Any]:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM call_stats_for($1)", user_id
            )
            return dict(row) if row else {}
