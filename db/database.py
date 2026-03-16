"""
db/database.py — Async PostgreSQL via asyncpg + Supabase Supavisor pooler
statement_cache_size=0 is REQUIRED for transaction mode (port 6543)
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
        raise RuntimeError("Database pool not initialised")
    return _pool


async def init_db():
    global _pool
    try:
        _pool = await asyncpg.create_pool(
            host=SUPABASE_DB_HOST,
            port=SUPABASE_DB_PORT,
            database=SUPABASE_DB_NAME,
            user=SUPABASE_DB_USER,
            password=SUPABASE_DB_PASS,
            min_size=1,
            max_size=5,
            ssl="require",
            command_timeout=30,
            statement_cache_size=0,   # REQUIRED for Supabase transaction pooler
        )
        async with _pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        log.info(f"✅ Connected to Supabase at {SUPABASE_DB_HOST}:{SUPABASE_DB_PORT}")
    except Exception as e:
        log.error(f"❌ PostgreSQL connection failed: {e}")
        _pool = None
        raise


async def close_db():
    global _pool
    if _pool:
        await _pool.close()


# ── SETTINGS ──────────────────────────────────────────────────

async def get_setting(key: str, default: str = "") -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        val = await conn.fetchval("SELECT value FROM settings WHERE key = $1", key)
        return val if val is not None else default


async def set_setting(key: str, value: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO settings (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()
        """, key, value)


async def get_all_settings() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in rows}


# ── CALLS ─────────────────────────────────────────────────────

async def upsert_call(sid: str, data: dict) -> Optional[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
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
            sid, data.get("phone",""), data.get("from_number",""),
            data.get("status","ringing"), data.get("hot_lead",False),
            data.get("duration_sec",0), data.get("agent_name","Sara"),
            data.get("agency_name",""),
        )
        return str(row["id"]) if row else None


async def update_call(sid: str, **kwargs):
    pool = await get_pool()
    if not kwargs: return
    fields, values = [], [sid]
    for i,(k,v) in enumerate(kwargs.items(), start=2):
        fields.append(f"{k}=${i}"); values.append(v)
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE calls SET {','.join(fields)},updated_at=NOW() WHERE sid=$1",
            *values
        )


async def finalize_call(sid: str, status: str, duration_sec: int,
                        recording_url: str, recording_path: str, transcript: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE calls SET status=$2, duration_sec=$3, recording_url=$4,
                recording_path=$5, transcript=$6, ended_at=NOW(), updated_at=NOW()
            WHERE sid=$1
        """, sid, status, duration_sec, recording_url, recording_path, transcript)


async def set_recording(sid: str, recording_url: str, recording_path: str):
    """Set recording URL separately (called from recording-status webhook)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE calls SET recording_url=$2, recording_path=$3, updated_at=NOW()
            WHERE sid=$1
        """, sid, recording_url, recording_path)


async def insert_message(call_sid: str, role: str, content: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        call_id = await conn.fetchval("SELECT id FROM calls WHERE sid=$1", call_sid)
        if call_id:
            await conn.execute("""
                INSERT INTO messages (call_id, call_sid, role, content)
                VALUES ($1,$2,$3,$4)
            """, call_id, call_sid, role, content)


async def get_calls(limit=100, offset=0, status=None, hot_only=False, search=None) -> list:
    pool = await get_pool()
    conds, vals, i = [], [], 1
    if status and status != "all":
        conds.append(f"status=${i}"); vals.append(status); i+=1
    if hot_only:
        conds.append("hot_lead=TRUE")
    if search:
        conds.append(f"phone ILIKE ${i}"); vals.append(f"%{search}%"); i+=1
    where = ("WHERE "+" AND ".join(conds)) if conds else ""
    vals.extend([limit, offset])
    sql = f"""
        SELECT id,sid,phone,status,hot_lead,duration_sec,
               started_at,ended_at,recording_url,transcript,agent_name,agency_name
        FROM calls {where}
        ORDER BY started_at DESC LIMIT ${i} OFFSET ${i+1}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *vals)
        return [dict(r) for r in rows]


async def get_call_messages(call_sid: str) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT role,content,created_at FROM messages
            WHERE call_sid=$1 ORDER BY created_at ASC
        """, call_sid)
        return [dict(r) for r in rows]


async def get_stats() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM call_stats")
        return dict(row) if row else {}


async def get_total_count(status=None, hot_only=False, search=None) -> int:
    pool = await get_pool()
    conds, vals, i = [], [], 1
    if status and status != "all":
        conds.append(f"status=${i}"); vals.append(status); i+=1
    if hot_only:
        conds.append("hot_lead=TRUE")
    if search:
        conds.append(f"phone ILIKE ${i}"); vals.append(f"%{search}%"); i+=1
    where = ("WHERE "+" AND ".join(conds)) if conds else ""
    async with pool.acquire() as conn:
        return await conn.fetchval(f"SELECT COUNT(*) FROM calls {where}", *vals)
