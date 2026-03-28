"""
db/repositories/calls.py
All database queries related to calls and messages.
Repository pattern — no SQL outside this file.
"""
from typing import Optional
from db.database import get_pool
from app.core.logging import get_logger

log = get_logger(__name__)


async def upsert_call(sid: str, data: dict) -> Optional[str]:
    """Insert or update a call record. Returns UUID."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO calls (sid, phone, from_number, status, hot_lead,
                               duration_sec, started_at, agent_name, agency_name, provider)
            VALUES ($1,$2,$3,$4,$5,$6,NOW(),$7,$8,$9)
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
            data.get("provider", "twilio"),
        )
        return str(row["id"]) if row else None


async def update_call(sid: str, **kwargs) -> None:
    """Update arbitrary fields on a call."""
    pool = await get_pool()
    if not kwargs:
        return
    fields, values = [], [sid]
    for i, (k, v) in enumerate(kwargs.items(), start=2):
        fields.append(f"{k}=${i}")
        values.append(v)
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE calls SET {','.join(fields)},updated_at=NOW() WHERE sid=$1",
            *values,
        )


async def finalize_call(sid: str, status: str, duration_sec: int,
                        transcript: str) -> None:
    """Mark call complete — recording handled separately by recording webhook."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE calls SET
                status=$2, duration_sec=$3, transcript=$4,
                ended_at=NOW(), updated_at=NOW()
            WHERE sid=$1
        """, sid, status, duration_sec, transcript)


async def set_recording(sid: str, recording_url: str, recording_path: str) -> None:
    """Set recording URL after Supabase Storage upload completes."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE calls SET recording_url=$2, recording_path=$3, updated_at=NOW()
            WHERE sid=$1
        """, sid, recording_url, recording_path)


async def insert_message(call_sid: str, role: str, content: str) -> None:
    """Append a conversation message to the messages table."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        call_id = await conn.fetchval("SELECT id FROM calls WHERE sid=$1", call_sid)
        if call_id:
            await conn.execute("""
                INSERT INTO messages (call_id, call_sid, role, content)
                VALUES ($1,$2,$3,$4)
            """, call_id, call_sid, role, content)


async def get_calls(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    hot_only: bool = False,
    search: Optional[str] = None,
) -> list[dict]:
    """Fetch paginated call list for dashboard."""
    pool = await get_pool()
    conds, vals, i = [], [], 1

    if status and status != "all":
        conds.append(f"status=${i}"); vals.append(status); i += 1
    if hot_only:
        conds.append("hot_lead=TRUE")
    if search:
        conds.append(f"phone ILIKE ${i}"); vals.append(f"%{search}%"); i += 1

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    vals.extend([limit, offset])

    sql = f"""
        SELECT id, sid, phone, status, hot_lead, duration_sec,
               started_at, ended_at, recording_url, transcript,
               agent_name, agency_name, provider
        FROM calls {where}
        ORDER BY started_at DESC
        LIMIT ${i} OFFSET ${i+1}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *vals)
        return [dict(r) for r in rows]


async def get_call_messages(call_sid: str) -> list[dict]:
    """Get all messages for a call ordered by time."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT role, content, created_at
            FROM messages WHERE call_sid=$1
            ORDER BY created_at ASC
        """, call_sid)
        return [dict(r) for r in rows]


async def get_stats() -> dict:
    """Aggregate stats for dashboard stat cards."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM call_stats")
        return dict(row) if row else {}


async def get_total_count(
    status: Optional[str] = None,
    hot_only: bool = False,
    search: Optional[str] = None,
) -> int:
    """Count calls matching filters for pagination."""
    pool = await get_pool()
    conds, vals, i = [], [], 1
    if status and status != "all":
        conds.append(f"status=${i}"); vals.append(status); i += 1
    if hot_only:
        conds.append("hot_lead=TRUE")
    if search:
        conds.append(f"phone ILIKE ${i}"); vals.append(f"%{search}%"); i += 1
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    async with pool.acquire() as conn:
        return await conn.fetchval(f"SELECT COUNT(*) FROM calls {where}", *vals)
