"""
app.db.session
──────────────
PostgreSQL connection pool (asyncpg) configured for Supabase Supavisor
transaction pooler. `statement_cache_size=0` is MANDATORY in that mode.
"""
from __future__ import annotations

import asyncpg

from app.core.config import get_settings
from app.core.exceptions import ConfigurationError
from app.core.logging import get_logger

log = get_logger(__name__)

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    """Initialise the global connection pool. Safe to call once at startup."""
    global _pool
    if _pool is not None:
        return _pool

    s = get_settings()
    if not s.supabase_db_host or not s.supabase_db_pass:
        raise ConfigurationError(
            "Supabase DB credentials not set — check SUPABASE_DB_HOST / SUPABASE_DB_PASS."
        )

    try:
        _pool = await asyncpg.create_pool(
            host=s.supabase_db_host,
            port=s.supabase_db_port,
            database=s.supabase_db_name,
            user=s.supabase_db_user,
            password=s.supabase_db_pass,
            min_size=s.supabase_db_pool_min,
            max_size=s.supabase_db_pool_max,
            ssl="require",
            command_timeout=30,
            # Mandatory for Supavisor transaction pooler (port 6543):
            statement_cache_size=0,
        )
        async with _pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        log.info("✅ Connected to Postgres at %s:%s", s.supabase_db_host, s.supabase_db_port)
        return _pool
    except Exception as exc:
        log.exception("❌ Postgres connection failed: %s", exc)
        _pool = None
        raise


def get_pool() -> asyncpg.Pool:
    """Return the pool, raising if not initialised."""
    if _pool is None:
        raise RuntimeError("Database pool not initialised. Call init_pool() at startup.")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
