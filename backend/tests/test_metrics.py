"""Metric snapshot logic: pure functions first, then the real queries."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from analytics_api.domain.events import validate_event
from analytics_api.processing.aggregates import INSERT_BATCH_SQL, batch_parameters
from analytics_api.processing.metrics import (
    LivePipeline,
    MetricsReader,
    Window,
    compute_kpis,
    dense_series,
    rank_dimension,
)

from .factories import event_payload

NOW = datetime(2026, 9, 12, 14, 37, 25, tzinfo=UTC)
LIVE = LivePipeline(
    events_per_second=0, freshness_lag_ms=None, queued_events=0, connected_clients=0
)


def row(bucket: datetime, event_type: str, count: int, amount: str, **extra: Any) -> dict[str, Any]:
    return {
        "bucket": bucket,
        "event_type": event_type,
        "event_count": count,
        "amount_sum": Decimal(amount),
        **extra,
    }


def test_window_includes_the_current_partial_minute() -> None:
    window = Window.ending_at(NOW, 60)
    assert window.current == datetime(2026, 9, 12, 14, 37, tzinfo=UTC)
    assert window.start == datetime(2026, 9, 12, 13, 38, tzinfo=UTC)
    assert window.previous_start == datetime(2026, 9, 12, 12, 38, tzinfo=UTC)


def test_kpis() -> None:
    kpis = compute_kpis(
        {
            "order_placed": (40, Decimal("9000")),
            "order_paid": (30, Decimal("7500.10")),
            "order_cancelled": (6, Decimal("1200")),
        }
    )
    assert kpis.revenue_mad == 7500.10
    assert kpis.orders == 40
    assert kpis.average_order_value_mad == 250.0
    assert kpis.cancellation_rate == 0.15
    assert kpis.shipped_orders == 0


def test_kpis_without_orders_have_no_ratios() -> None:
    kpis = compute_kpis({})
    assert (kpis.average_order_value_mad, kpis.cancellation_rate, kpis.revenue_mad) == (
        None,
        None,
        0.0,
    )


def test_dense_series_fills_gaps_with_zero() -> None:
    start = datetime(2026, 9, 12, 14, 0, tzinfo=UTC)
    rows = [
        row(start, "order_paid", 2, "100.50"),
        row(start + timedelta(minutes=3), "order_placed", 5, "900"),
        row(start + timedelta(minutes=3), "order_cancelled", 1, "80"),
    ]
    points = dense_series(rows, start, start + timedelta(minutes=4), timedelta(minutes=1))
    assert [p.revenue_mad for p in points] == [100.5, 0, 0, 0, 0]
    assert [p.orders for p in points] == [0, 0, 0, 5, 0]
    assert [p.cancelled for p in points] == [0, 0, 0, 1, 0]


def test_ranking_uses_paid_revenue_then_orders() -> None:
    b = NOW
    rows = [
        row(b, "order_paid", 3, "500", dimension="city", dim_value="Rabat"),
        row(b, "order_placed", 9, "900", dimension="city", dim_value="Rabat"),
        row(b, "order_paid", 1, "800", dimension="city", dim_value="Fès"),
        row(b, "order_placed", 4, "100", dimension="city", dim_value="Agadir"),
        row(b, "order_placed", 1, "100", dimension="city", dim_value="Oujda"),
        row(b, "order_paid", 5, "999", dimension="category", dim_value="books"),
    ]
    ranked = rank_dimension(rows, "city")
    assert [(r.name, r.revenue_mad, r.orders) for r in ranked] == [
        ("Fès", 800.0, 0),
        ("Rabat", 500.0, 9),
        ("Agadir", 0.0, 4),
        ("Oujda", 0.0, 1),
    ]


async def test_snapshot_from_database(db: asyncpg.Pool) -> None:
    def event(minutes_ago: float, event_type: str, amount: str, **kw: Any) -> Any:
        at = NOW - timedelta(minutes=minutes_ago)
        return validate_event(
            event_payload(
                event_type=event_type, occurred_at=at.isoformat(), amount_mad=amount, **kw
            )
        )

    events = [
        event(1, "order_placed", "100", category="books"),
        event(1, "order_placed", "300", category="electronics"),
        event(0.2, "order_paid", "300", category="electronics", city="Tanger"),
        event(5, "order_paid", "100", category="books", city="Rabat"),
        event(10, "order_cancelled", "50", category="books"),
        event(70, "order_paid", "1000", category="home"),  # previous window only
        event(200, "order_paid", "7", category="toys"),  # outside both windows
    ]
    await db.fetchrow(INSERT_BATCH_SQL, *batch_parameters(events))

    reader = MetricsReader(db, window_minutes=60, hourly_hours=24)
    snapshot = await reader.snapshot(NOW, through_seq=7, live=LIVE)

    assert snapshot.kpis.revenue_mad == 400.0
    assert snapshot.kpis.orders == 2
    assert snapshot.kpis.cancellation_rate == 0.5
    assert snapshot.kpis.average_order_value_mad == 200.0
    assert snapshot.previous_kpis.revenue_mad == 1000.0
    assert [c.name for c in snapshot.top_categories] == ["electronics", "books"]
    assert snapshot.top_cities[0].name == "Tanger"
    assert len(snapshot.revenue_per_minute) == 60
    assert snapshot.revenue_per_minute[-1].revenue_mad == 300.0
    assert len(snapshot.revenue_per_hour) == 24
    assert sum(p.revenue_mad for p in snapshot.revenue_per_hour) == 1407.0
    assert {s.status.value: s.count for s in snapshot.orders_by_status} == {
        "order_placed": 2,
        "order_paid": 2,
        "order_shipped": 0,
        "order_cancelled": 1,
    }
