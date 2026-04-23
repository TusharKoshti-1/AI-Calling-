"""
app.db.repositories.calls
─────────────────────────
All SQL reads/writes for the `calls` aggregate (calls + derived stats).
"""
from __future__ import annotations

from typing import Any

from app.db.session import get_pool


class CallsRepository:
    """All CRUD on the `calls` table. Pure data access; no business logic."""

    # ── Writes ────────────────────────────────────────────────
    async def upsert(self, sid: str, data: dict[str, Any]) -> str | None:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO calls (sid, phone, from_number, status, hot_lead,
                                   duration_sec, started_at, agent_name, agency_name)
                VALUES ($1,$2,$3,$4,$5,$6,NOW(),$7,$8)
                ON CONFLICT (sid) DO UPDATE SET
                    status       = EXCLUDED.status,
                    hot_lead     = GREATEST(calls.hot_lead, EXCLUDED.hot_lead),
                    duration_sec = EXCLUDED.duration_sec,
                    updated_at   = NOW()
                RETURNING id
                """,
                sid,
                data.get("phone", ""),
                data.get("from_number", ""),
                data.get("status", "ringing"),
                data.get("hot_lead", False),
                data.get("duration_sec", 0),
                data.get("agent_name", "Sara"),
                data.get("agency_name", ""),
            )
            return str(row["id"]) if row else None

    async def update(self, sid: str, **fields: Any) -> None:
        if not fields:
            return
        # Whitelist to prevent SQL injection via field names.
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

    async def finalize(
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

    async def set_recording(self, sid: str, recording_url: str, recording_path: str) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE calls SET recording_url=$2, recording_path=$3, updated_at=NOW()
                WHERE sid=$1
                """,
                sid, recording_url, recording_path,
            )

    # ── Reads ─────────────────────────────────────────────────
    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        hot_only: bool = False,
        search: str | None = None,
    ) -> list[dict]:
        conditions, values, idx = [], [], 1
        if status and status != "all":
            conditions.append(f"status = ${idx}")
            values.append(status)
            idx += 1
        if hot_only:
            conditions.append("hot_lead = TRUE")
        if search:
            conditions.append(f"phone ILIKE ${idx}")
            values.append(f"%{search}%")
            idx += 1

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
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
        status: str | None = None,
        hot_only: bool = False,
        search: str | None = None,
    ) -> int:
        conditions, values, idx = [], [], 1
        if status and status != "all":
            conditions.append(f"status = ${idx}")
            values.append(status)
            idx += 1
        if hot_only:
            conditions.append("hot_lead = TRUE")
        if search:
            conditions.append(f"phone ILIKE ${idx}")
            values.append(f"%{search}%")
            idx += 1
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT COUNT(*) FROM calls {where}"
        pool = get_pool()
        async with pool.acquire() as conn:
            result = await conn.fetchval(sql, *values)
            return int(result or 0)

    async def stats(self) -> dict[str, Any]:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM call_stats")
            return dict(row) if row else {}
