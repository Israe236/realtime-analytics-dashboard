"""The incremental aggregates must always equal a from-scratch GROUP BY over raw events."""

import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from analytics_api.domain.events import (
    Category,
    EventType,
    OrderEvent,
    PaymentMethod,
    validate_event,
)
from analytics_api.processing.aggregates import (
    INSERT_BATCH_SQL,
    REBUILD_EXPECTED_SQL,
    batch_parameters,
)

from .factories import event_payload

CITIES = ["Casablanca", "Rabat", "Marrakech", "Fès", "Tanger", "Agadir"]
BASE = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


def make_event(**overrides: Any) -> OrderEvent:
    return validate_event(event_payload(**overrides))


async def write(db: asyncpg.Pool, events: Sequence[OrderEvent]) -> asyncpg.Record:
    row = await db.fetchrow(INSERT_BATCH_SQL, *batch_parameters(events))
    assert row is not None
    return row


async def assert_aggregates_consistent(db: asyncpg.Pool) -> None:
    for table, unit in (("agg_minute", "minute"), ("agg_hour", "hour")):
        expected = {
            (r["dimension"], r["bucket"], r["dim_value"], r["event_type"]): (
                r["event_count"],
                r["amount_sum"],
            )
            for r in await db.fetch(REBUILD_EXPECTED_SQL, unit)
        }
        actual = {
            (r["dimension"], r["bucket"], r["dim_value"], r["event_type"]): (
                r["event_count"],
                r["amount_sum"],
            )
            for r in await db.fetch(f"SELECT * FROM {table}")
        }
        assert actual == expected, f"{table} diverged from a full recompute"


async def test_random_batches_with_duplicates_match_full_recompute(db: asyncpg.Pool) -> None:
    rng = random.Random(42)
    sent: list[OrderEvent] = []
    for _ in range(25):
        batch: list[OrderEvent] = []
        for _ in range(rng.randint(1, 300)):
            if sent and rng.random() < 0.1:
                batch.append(rng.choice(sent))  # duplicate from an earlier batch
            elif batch and rng.random() < 0.05:
                batch.append(rng.choice(batch))  # duplicate inside the same batch
            else:
                batch.append(
                    make_event(
                        event_type=rng.choice(list(EventType)),
                        occurred_at=(
                            BASE + timedelta(seconds=rng.randint(0, 3 * 3600))
                        ).isoformat(),
                        amount_mad=f"{rng.uniform(1, 5000):.2f}",
                        category=rng.choice(list(Category)),
                        city=rng.choice(CITIES),
                        payment_method=rng.choice(list(PaymentMethod)),
                    )
                )
        sent.extend(batch)
        await write(db, batch)

    await assert_aggregates_consistent(db)
    unique = len({e.event_id for e in sent})
    assert await db.fetchval("SELECT count(*) FROM events") == unique
    stats = await db.fetchrow(
        "SELECT sum(accepted) AS accepted, sum(duplicates) AS duplicates FROM ingest_stats_minute"
    )
    assert stats is not None
    assert stats["accepted"] == unique
    assert stats["accepted"] + stats["duplicates"] == len(sent)


async def test_every_dimension_gets_one_row_per_event_type(db: asyncpg.Pool) -> None:
    await write(
        db,
        [
            make_event(
                occurred_at=BASE.isoformat(),
                amount_mad="100.00",
                category="books",
                city="Rabat",
                payment_method="wallet",
            ),
            make_event(
                occurred_at=(BASE + timedelta(seconds=30)).isoformat(),
                amount_mad="50.50",
                category="books",
                city="Agadir",
                payment_method="wallet",
            ),
        ],
    )
    rows = await db.fetch(
        "SELECT dimension, dim_value, event_count, amount_sum FROM agg_minute ORDER BY 1, 2"
    )
    assert [(r["dimension"], r["dim_value"], r["event_count"], r["amount_sum"]) for r in rows] == [
        ("all", "", 2, Decimal("150.50")),
        ("category", "books", 2, Decimal("150.50")),
        ("city", "Agadir", 1, Decimal("50.50")),
        ("city", "Rabat", 1, Decimal("100.00")),
        ("payment_method", "wallet", 2, Decimal("150.50")),
    ]


async def test_late_event_updates_its_own_past_bucket(db: asyncpg.Pool) -> None:
    await write(db, [make_event(occurred_at=(BASE + timedelta(minutes=65)).isoformat())])
    late = BASE + timedelta(minutes=12, seconds=7)
    await write(db, [make_event(occurred_at=late.isoformat(), amount_mad="10.00")])

    minute = await db.fetchrow(
        "SELECT event_count, amount_sum FROM agg_minute WHERE dimension = 'all' AND bucket = $1",
        BASE + timedelta(minutes=12),
    )
    hour = await db.fetchval(
        "SELECT event_count FROM agg_hour WHERE dimension = 'all' AND bucket = $1", BASE
    )
    assert minute is not None
    assert (minute["event_count"], minute["amount_sum"]) == (1, Decimal("10.00"))
    assert hour == 1
    await assert_aggregates_consistent(db)


async def test_statement_reports_inserted_rows_and_max_seq(db: asyncpg.Pool) -> None:
    batch = [make_event() for _ in range(3)]
    first = await write(db, batch)
    assert (first["inserted"], first["max_seq"]) == (3, 3)

    replay = await write(db, batch)
    assert (replay["inserted"], replay["max_seq"]) == (0, None)
    assert await db.fetchval("SELECT sum(event_count) FROM agg_minute WHERE dimension = 'all'") == 3
