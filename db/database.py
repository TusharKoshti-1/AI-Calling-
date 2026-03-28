"""
db/database.py
Async PostgreSQL pool via asyncpg + Supabase Supavisor pooler.
statement_cache_size=0 is REQUIRED for transaction mode (port 6543).
"""
import asyncpg
from typing import Optional
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call init_db() first")
    return _pool


async def init_db() -> None:
    global _pool
    try:
        _pool = await asyncpg.create_pool(
            host=settings.SUPABASE_DB_HOST,
            port=settings.SUPABASE_DB_PORT,
            database=settings.SUPABASE_DB_NAME,
            user=settings.SUPABASE_DB_USER,
            password=settings.SUPABASE_DB_PASS,
            min_size=1,
            max_size=5,
            ssl="require",
            command_timeout=30,
            statement_cache_size=0,   # Required for Supabase transaction pooler
        )
        async with _pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        log.info(f"✅ PostgreSQL connected → {settings.SUPABASE_DB_HOST}:{settings.SUPABASE_DB_PORT}")
    except Exception as e:
        log.error(f"❌ PostgreSQL connection failed: {e}")
        _pool = None
        raise


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        log.info("PostgreSQL pool closed")
