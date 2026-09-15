from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg

from analytics_api.domain.events import validate_event
from analytics_api.processing.aggregates import INSERT_BATCH_SQL, batch_parameters
from analytics_api.processing.partitions import (
    drop_expired_partitions,
    ensure_daily_partitions,
    list_daily_partitions,
    partition_name,
)

from .factories import event_payload


async def insert(db: asyncpg.Pool, at: datetime, count: int = 1, **overrides: Any) -> None:
    events = [
        validate_event(event_payload(occurred_at=at.isoformat(), **overrides)) for _ in range(count)
    ]
    await db.fetchrow(INSERT_BATCH_SQL, *batch_parameters(events))


async def test_new_events_land_in_todays_partition(db: asyncpg.Pool) -> None:
    now = datetime.now(UTC)
    await ensure_daily_partitions(db, now.date(), days_back=0, days_ahead=0)
    await insert(db, now)
    assert await db.fetchval("SELECT tableoid::regclass::text FROM events") == partition_name(
        now.date()
    )


async def test_expired_days_are_dropped_whole_and_aggregates_survive(db: asyncpg.Pool) -> None:
    day = date(2020, 1, 15)
    names = [partition_name(day + timedelta(days=offset)) for offset in (-1, 0, 1)]

    assert await ensure_daily_partitions(db, day, days_back=1, days_ahead=1) == names
    assert await ensure_daily_partitions(db, day, days_back=1, days_ahead=1) == []  # idempotent

    noon = datetime(2020, 1, 15, 12, 0, tzinfo=UTC)
    await insert(db, noon, count=3)
    await insert(db, datetime.now(UTC))
    assert (
        await db.fetchval(
            "SELECT count(*) FROM events WHERE tableoid = $1::regclass", partition_name(day)
        )
        == 3
    )

    # The whole of 2020-01-16 ends at 2020-01-17T00:00, so all three partitions have expired.
    dropped = await drop_expired_partitions(db, datetime(2020, 1, 17, tzinfo=UTC))
    assert dropped == names
    assert not set(names) & set((await list_daily_partitions(db)).values())
    assert await db.fetchval("SELECT count(*) FROM events") == 1

    # Aggregates are separate tables: history outlives the raw rows.
    kept = await db.fetchval(
        "SELECT sum(event_count) FROM agg_hour WHERE dimension = 'all' AND bucket = $1",
        datetime(2020, 1, 15, 12, tzinfo=UTC),
    )
    assert kept == 3


async def test_dedupe_key_includes_the_timestamp(db: asyncpg.Pool) -> None:
    now = datetime.now(UTC)
    event_id = "11111111-2222-3333-4444-555555555555"

    # A retry carries the same payload, so it is still recognised as a duplicate...
    await insert(db, now, event_id=event_id)
    await insert(db, now, event_id=event_id)
    assert await db.fetchval("SELECT count(*) FROM events") == 1

    # ...but the same id with a different timestamp is stored as a second event. This is the
    # documented trade-off of partitioning by occurred_at (see DECISIONS.md).
    await insert(db, now - timedelta(minutes=1), event_id=event_id)
    assert await db.fetchval("SELECT count(*) FROM events") == 2
