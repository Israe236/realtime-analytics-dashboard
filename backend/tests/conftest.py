"""Shared fixtures.

Database tests run against a real PostgreSQL (the docker compose service locally, a
service container in CI). A throwaway ``analytics_test`` database is recreated once per
test session, and tables are truncated before every test that asks for ``db``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from analytics_api.db.migrate import apply_migrations

ADMIN_URL = os.environ.get(
    "TEST_DATABASE_ADMIN_URL", "postgresql://analytics:analytics@localhost:55432/postgres"
)
TEST_DB_NAME = "analytics_test"
TABLES = ("events", "dead_letter_events", "agg_minute", "agg_hour", "ingest_stats_minute", "alerts")


@pytest.fixture(scope="session")
async def database_url() -> AsyncIterator[str]:
    admin = await asyncpg.connect(ADMIN_URL)
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)")
        await admin.execute(f"CREATE DATABASE {TEST_DB_NAME}")
    finally:
        await admin.close()
    url = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"
    conn = await asyncpg.connect(url)
    try:
        await apply_migrations(conn)
    finally:
        await conn.close()
    yield url


@pytest.fixture(scope="session")
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    yield pool
    await pool.close()


@pytest.fixture
async def db(pool: asyncpg.Pool) -> asyncpg.Pool:
    await pool.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY")
    return pool
