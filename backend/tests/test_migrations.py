import asyncpg

from analytics_api.db.migrate import apply_migrations

from .conftest import TABLES


async def test_migrations_are_idempotent(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        # The session fixture already applied everything; a second run must be a no-op.
        assert await apply_migrations(conn) == []
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    assert set(TABLES) | {"schema_migrations"} <= {r["table_name"] for r in rows}


async def test_one_firing_alert_per_rule_is_enforced(db: asyncpg.Pool) -> None:
    insert = (
        "INSERT INTO alerts (rule, severity, state, message, threshold, fired_at)"
        " VALUES ('cancellation_rate', 'warning', 'firing', 'm', 0.25, now())"
    )
    await db.execute(insert)
    try:
        await db.execute(insert)
    except asyncpg.UniqueViolationError:
        pass
    else:
        raise AssertionError("second firing alert for the same rule was accepted")
