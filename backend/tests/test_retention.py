from datetime import UTC, datetime, timedelta

import asyncpg

from analytics_api.config import Settings
from analytics_api.domain.events import validate_event
from analytics_api.processing.aggregates import INSERT_BATCH_SQL, batch_parameters
from analytics_api.processing.retention import RetentionJob, RetentionPolicy

from .factories import event_payload

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


async def insert_events(db: asyncpg.Pool, days_ago: float, count: int) -> None:
    at = (NOW - timedelta(days=days_ago)).isoformat()
    events = [validate_event(event_payload(occurred_at=at)) for _ in range(count)]
    await db.fetchrow(INSERT_BATCH_SQL, *batch_parameters(events))


async def count(db: asyncpg.Pool, table: str) -> int:
    value = await db.fetchval(f"SELECT count(*) FROM {table}")
    return int(value)


async def test_deletes_expired_rows_in_batches(db: asyncpg.Pool) -> None:
    # 10 days before NOW there is no daily partition, so these rows sit in the default
    # partition and must be removed with batched row deletes.
    await insert_events(db, days_ago=10, count=25)
    await insert_events(db, days_ago=1, count=5)  # kept
    await db.execute(
        "INSERT INTO dead_letter_events (received_at, reason, payload) VALUES ($1, 'x', 'old')",
        NOW - timedelta(days=30),
    )

    # batch_size 7 forces several DELETE statements for the 25 old events.
    policy = RetentionPolicy(raw_days=8, minute_days=8, hour_days=400, batch_size=7)
    job = RetentionJob(db, policy, interval_s=3600)
    result = await job.run_once(NOW)

    assert result["events_default"] == 25
    assert result["dead_letter_events"] == 1
    assert result["agg_hour"] == 0  # hourly history is kept for 400 days
    assert await count(db, "events") == 5
    assert await db.fetchval("SELECT min(bucket) FROM agg_minute") >= NOW - timedelta(days=8)
    old_hours = await db.fetchval(
        "SELECT count(*) FROM agg_hour WHERE bucket < $1", NOW - timedelta(days=8)
    )
    assert old_hours > 0

    # Idempotent: a second run creates, drops and deletes nothing.
    assert not any((await job.run_once(NOW)).values())


def test_raw_retention_outlives_the_maximum_accepted_event_age() -> None:
    # If raw rows could disappear while an event of that age is still accepted, a retry of
    # that event would no longer be recognised as a duplicate and would be counted twice.
    settings = Settings()
    assert settings.retention_raw_days * 86_400 > settings.max_event_age_s
