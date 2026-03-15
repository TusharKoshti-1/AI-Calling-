"""
db/database.py — Async PostgreSQL connection via asyncpg
Connects to Supabase PostgreSQL directly (not via REST API)
"""
import asyncpg
import logging
from typing import Optional
from config import (
    SUPABASE_DB_HOST, SUPABASE_DB_PORT, SUPABASE_DB_NAME,
    SUPABASE_DB_USER, SUPABASE_DB_PASS
)

log = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_db() first")
    return _pool


async def init_db():
    """Create connection pool — call on app startup"""
    global _pool
    try:
        _pool = await asyncpg.create_pool(
            host=SUPABASE_DB_HOST,
            port=SUPABASE_DB_PORT,
            database=SUPABASE_DB_NAME,
            user=SUPABASE_DB_USER,
            password=SUPABASE_DB_PASS,
            min_size=2,
            max_size=10,
            ssl="require",          # Supabase requires SSL
            command_timeout=30,
        )
        # Test connection
        async with _pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        log.info("✅ PostgreSQL pool connected to Supabase")
    except Exception as e:
        log.error(f"❌ PostgreSQL connection failed: {e}")
        _pool = None
        raise


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        log.info("PostgreSQL pool closed")


# ── CALL OPERATIONS ───────────────────────────────────────────

async def upsert_call(sid: str, data: dict) -> Optional[str]:
    """Insert or update a call record, return UUID"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO calls (sid, phone, from_number, status, hot_lead,
                               duration_sec, started_at, agent_name, agency_name)
            VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8)
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


async def update_call(sid: str, **kwargs):
    """Update specific fields on a call"""
    pool = await get_pool()
    if not kwargs:
        return

    # Build dynamic SET clause
    fields = []
    values = [sid]
    for i, (k, v) in enumerate(kwargs.items(), start=2):
        fields.append(f"{k} = ${i}")
        values.append(v)

    sql = f"UPDATE calls SET {', '.join(fields)}, updated_at = NOW() WHERE sid = $1"
    async with pool.acquire() as conn:
        await conn.execute(sql, *values)


async def finalize_call(sid: str, status: str, duration_sec: int,
                         recording_url: str, recording_path: str, transcript: str):
    """Mark call as complete with all final data"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE calls SET
                status        = $2,
                duration_sec  = $3,
                recording_url = $4,
                recording_path= $5,
                transcript    = $6,
                ended_at      = NOW(),
                updated_at    = NOW()
            WHERE sid = $1
        """, sid, status, duration_sec, recording_url, recording_path, transcript)


async def insert_message(call_sid: str, role: str, content: str):
    """Insert a single transcript message"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Get call UUID
        call_id = await conn.fetchval("SELECT id FROM calls WHERE sid = $1", call_sid)
        if call_id:
            await conn.execute("""
                INSERT INTO messages (call_id, call_sid, role, content)
                VALUES ($1, $2, $3, $4)
            """, call_id, call_sid, role, content)


async def get_calls(limit: int = 100, offset: int = 0, status: str = None,
                    hot_only: bool = False, search: str = None) -> list:
    """Fetch calls for dashboard — newest first"""
    pool = await get_pool()
    conditions = []
    values = []
    i = 1

    if status and status != "all":
        conditions.append(f"status = ${i}")
        values.append(status); i += 1

    if hot_only:
        conditions.append("hot_lead = TRUE")

    if search:
        conditions.append(f"phone ILIKE ${i}")
        values.append(f"%{search}%"); i += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    values.extend([limit, offset])

    sql = f"""
        SELECT id, sid, phone, status, hot_lead, duration_sec,
               started_at, ended_at, recording_url, transcript,
               agent_name, agency_name, created_at
        FROM calls
        {where}
        ORDER BY started_at DESC
        LIMIT ${i} OFFSET ${i+1}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *values)
        return [dict(r) for r in rows]


async def get_call_messages(call_sid: str) -> list:
    """Get all messages for a call"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT role, content, created_at
            FROM messages WHERE call_sid = $1
            ORDER BY created_at ASC
        """, call_sid)
        return [dict(r) for r in rows]


async def get_stats() -> dict:
    """Get dashboard statistics"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM call_stats")
        return dict(row) if row else {}


async def get_total_count(status: str = None, hot_only: bool = False, search: str = None) -> int:
    """Get total number of calls matching filters"""
    pool = await get_pool()
    conditions = []
    values = []
    i = 1

    if status and status != "all":
        conditions.append(f"status = ${i}")
        values.append(status); i += 1
    if hot_only:
        conditions.append("hot_lead = TRUE")
    if search:
        conditions.append(f"phone ILIKE ${i}")
        values.append(f"%{search}%"); i += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    async with pool.acquire() as conn:
        return await conn.fetchval(f"SELECT COUNT(*) FROM calls {where}", *values)
