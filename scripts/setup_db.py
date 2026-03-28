"""
scripts/setup_db.py
Run once to create tables and seed default settings.
Usage: python scripts/setup_db.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db.database import init_db, close_db, get_pool
from app.core.logging import setup_logging, get_logger

setup_logging()
log = get_logger("setup_db")

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), '..', 'db', 'schema.sql')


async def run():
    log.info("Connecting to database...")
    await init_db()
    pool = await get_pool()

    log.info("Running schema...")
    schema = open(SCHEMA_PATH).read()

    # Split on double-newline to run statement groups
    async with pool.acquire() as conn:
        await conn.execute(schema)

    log.info("✅ Schema applied successfully")
    await close_db()


if __name__ == "__main__":
    asyncio.run(run())
