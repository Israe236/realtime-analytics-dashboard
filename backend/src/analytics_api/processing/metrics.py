"""Dashboard metrics, read from the aggregate tables.

A snapshot costs four small index range scans, whatever the traffic:

* the ``all`` dimension for the current and previous window (≤ 2 × 60 buckets × 4 event types),
* the ``all`` dimension of ``agg_hour`` for the last 24 hours,
* per category / city / payment method for the current window,
* ingest counters for the dead-letter rate.

They run in one read-only REPEATABLE READ transaction so all numbers in a snapshot come
from the same instant (otherwise the chart could include a batch that the KPI cards don't).

Everything that turns rows into messages is a pure function, tested without a database.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from analytics_api.domain.events import EventType
from analytics_api.realtime.protocol import (
    Kpis,
    PipelineHealth,
    RankedItem,
    SeriesPoint,
    SnapshotMessage,
    StatusCount,
)

Row = Mapping[str, Any]
ZERO = Decimal(0)
MINUTE = timedelta(minutes=1)
HOUR = timedelta(hours=1)

_MINUTE_SERIES_SQL = """
SELECT bucket, event_type, event_count, amount_sum
FROM agg_minute
WHERE dimension = 'all' AND bucket BETWEEN $1 AND $2
"""
_HOUR_SERIES_SQL = """
SELECT bucket, event_type, event_count, amount_sum
FROM agg_hour
WHERE dimension = 'all' AND bucket BETWEEN $1 AND $2
"""
_BREAKDOWN_SQL = """
SELECT dimension, dim_value, event_type,
       sum(event_count) AS event_count, sum(amount_sum) AS amount_sum
FROM agg_minute
WHERE dimension IN ('category', 'city', 'payment_method') AND bucket BETWEEN $1 AND $2
GROUP BY dimension, dim_value, event_type
"""
_INGEST_SQL = """
SELECT coalesce(sum(accepted), 0) AS accepted, coalesce(sum(rejected), 0) AS rejected
FROM ingest_stats_minute
WHERE bucket BETWEEN $1 AND $2
"""


@dataclass(frozen=True, slots=True)
class Window:
    """Minute buckets are labelled by their start; the current minute is still filling."""

    previous_start: datetime
    start: datetime
    current: datetime

    @classmethod
    def ending_at(cls, now: datetime, minutes: int) -> Window:
        current = now.replace(second=0, microsecond=0)
        start = current - (minutes - 1) * MINUTE
        return cls(previous_start=start - minutes * MINUTE, start=start, current=current)


@dataclass(frozen=True, slots=True)
class LivePipeline:
    """Pipeline numbers that live in process memory rather than in the database."""

    events_per_second: float
    freshness_lag_ms: float | None
    queued_events: int
    connected_clients: int


# ---- pure transformations ----------------------------------------------------------------


def _money(value: Decimal) -> float:
    return float(round(value, 2))


def sum_by_event_type(rows: Iterable[Row]) -> dict[str, tuple[int, Decimal]]:
    totals: dict[str, tuple[int, Decimal]] = {}
    for row in rows:
        count, amount = totals.get(row["event_type"], (0, ZERO))
        totals[row["event_type"]] = (count + row["event_count"], amount + row["amount_sum"])
    return totals


def compute_kpis(totals: Mapping[str, tuple[int, Decimal]]) -> Kpis:
    def get(event_type: EventType) -> tuple[int, Decimal]:
        return totals.get(event_type.value, (0, ZERO))

    placed = get(EventType.ORDER_PLACED)[0]
    paid, revenue = get(EventType.ORDER_PAID)
    cancelled = get(EventType.ORDER_CANCELLED)[0]
    return Kpis(
        revenue_mad=_money(revenue),
        orders=placed,
        paid_orders=paid,
        shipped_orders=get(EventType.ORDER_SHIPPED)[0],
        cancelled_orders=cancelled,
        average_order_value_mad=_money(revenue / paid) if paid else None,
        # Cancellations in the window can belong to orders placed before it, so this is an
        # operational ratio, not a per-order probability (it can exceed 1 in odd windows).
        cancellation_rate=round(cancelled / placed, 4) if placed else None,
    )


def dense_series(
    rows: Iterable[Row], start: datetime, end: datetime, step: timedelta
) -> list[SeriesPoint]:
    """One point per bucket from start to end inclusive; empty buckets are zeros.

    Charts need a continuous time axis: a minute without events must show as 0, not vanish.
    """
    by_bucket: dict[datetime, dict[str, tuple[int, Decimal]]] = defaultdict(dict)
    for row in rows:
        by_bucket[row["bucket"]][row["event_type"]] = (row["event_count"], row["amount_sum"])
    points: list[SeriesPoint] = []
    bucket = start
    while bucket <= end:
        counts = by_bucket.get(bucket, {})
        points.append(
            SeriesPoint(
                bucket=bucket,
                revenue_mad=_money(counts.get(EventType.ORDER_PAID.value, (0, ZERO))[1]),
                orders=counts.get(EventType.ORDER_PLACED.value, (0, ZERO))[0],
                cancelled=counts.get(EventType.ORDER_CANCELLED.value, (0, ZERO))[0],
            )
        )
        bucket += step
    return points


def rank_dimension(rows: Iterable[Row], dimension: str) -> list[RankedItem]:
    """Items of one dimension ordered by paid revenue, then by number of orders."""
    revenue: dict[str, Decimal] = defaultdict(lambda: ZERO)
    orders: dict[str, int] = defaultdict(int)
    for row in rows:
        if row["dimension"] != dimension:
            continue
        name = row["dim_value"]
        if row["event_type"] == EventType.ORDER_PAID.value:
            revenue[name] += row["amount_sum"]
        elif row["event_type"] == EventType.ORDER_PLACED.value:
            orders[name] += row["event_count"]
    names = set(revenue) | set(orders)
    ranked = sorted(names, key=lambda n: (-revenue[n], -orders[n], n))
    return [RankedItem(name=n, revenue_mad=_money(revenue[n]), orders=orders[n]) for n in ranked]


def build_snapshot(
    *,
    now: datetime,
    window: Window,
    window_minutes: int,
    through_seq: int,
    minute_rows: list[Row],
    hour_rows: list[Row],
    hour_start: datetime,
    hour_end: datetime,
    breakdown_rows: list[Row],
    accepted: int,
    rejected: int,
    live: LivePipeline,
) -> SnapshotMessage:
    current_rows = [r for r in minute_rows if r["bucket"] >= window.start]
    previous_rows = [r for r in minute_rows if r["bucket"] < window.start]
    totals = sum_by_event_type(current_rows)
    received = accepted + rejected
    return SnapshotMessage(
        generated_at=now,
        through_seq=through_seq,
        window_minutes=window_minutes,
        kpis=compute_kpis(totals),
        previous_kpis=compute_kpis(sum_by_event_type(previous_rows)),
        revenue_per_minute=dense_series(current_rows, window.start, window.current, MINUTE),
        revenue_per_hour=dense_series(hour_rows, hour_start, hour_end, HOUR),
        orders_by_status=[
            StatusCount(status=t, count=totals.get(t.value, (0, ZERO))[0]) for t in EventType
        ],
        top_categories=rank_dimension(breakdown_rows, "category"),
        top_cities=rank_dimension(breakdown_rows, "city"),
        payment_methods=rank_dimension(breakdown_rows, "payment_method"),
        pipeline=PipelineHealth(
            events_per_second=round(live.events_per_second, 1),
            dead_letter_rate=round(rejected / received, 4) if received else None,
            freshness_lag_ms=live.freshness_lag_ms,
            queued_events=live.queued_events,
            connected_clients=live.connected_clients,
        ),
    )


# ---- database access ------------------------------------------------------------------------


class MetricsReader:
    def __init__(self, pool: asyncpg.Pool, *, window_minutes: int, hourly_hours: int) -> None:
        self._pool = pool
        self._window_minutes = window_minutes
        self._hourly_hours = hourly_hours

    async def snapshot(
        self, now: datetime, *, through_seq: int, live: LivePipeline
    ) -> SnapshotMessage:
        window = Window.ending_at(now, self._window_minutes)
        hour_end = now.replace(minute=0, second=0, microsecond=0)
        hour_start = hour_end - (self._hourly_hours - 1) * HOUR
        async with (
            self._pool.acquire() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            minute_rows = await conn.fetch(
                _MINUTE_SERIES_SQL, window.previous_start, window.current
            )
            hour_rows = await conn.fetch(_HOUR_SERIES_SQL, hour_start, hour_end)
            breakdown_rows = await conn.fetch(_BREAKDOWN_SQL, window.start, window.current)
            ingest = await conn.fetchrow(_INGEST_SQL, window.start, window.current)
        assert ingest is not None
        return build_snapshot(
            now=now,
            window=window,
            window_minutes=self._window_minutes,
            through_seq=through_seq,
            # asyncpg Records behave like mappings but are not typed as such.
            minute_rows=[dict(r) for r in minute_rows],
            hour_rows=[dict(r) for r in hour_rows],
            hour_start=hour_start,
            hour_end=hour_end,
            breakdown_rows=[dict(r) for r in breakdown_rows],
            accepted=int(ingest["accepted"]),
            rejected=int(ingest["rejected"]),
            live=live,
        )
