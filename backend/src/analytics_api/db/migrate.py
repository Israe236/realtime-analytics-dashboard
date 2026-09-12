"""Minimal forward-only SQL migration runner.

Each ``migrations/NNN_name.sql`` file is applied once, inside a transaction, and recorded in
``schema_migrations``. A Postgres advisory lock makes concurrent runners (e.g. two API
containers starting together) wait for each other instead of racing.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import asyncpg
from asyncpg.pool import PoolConnectionProxy

from analytics_api.config import get_settings

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_ADVISORY_LOCK_ID = 7_142_001


async def apply_migrations(conn: asyncpg.Connection | PoolConnectionProxy) -> list[str]:
    """Apply pending migrations; return the versions applied by this call."""
    await conn.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK_ID)
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
        applied = {
            row["version"] for row in await conn.fetch("SELECT version FROM schema_migrations")
        }
        newly_applied: list[str] = []
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.stem in applied:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", path.stem)
            log.info("applied migration %s", path.stem)
            newly_applied.append(path.stem)
        return newly_applied
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK_ID)


async def _main() -> None:
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        applied = await apply_migrations(conn)
        print(f"applied: {applied or 'nothing (up to date)'}")
    finally:
        await conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
