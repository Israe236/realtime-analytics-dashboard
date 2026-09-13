from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from analytics_api.alerts.evaluator import AlertEvaluator
from analytics_api.alerts.rules import CancellationRateRule, RevenueDropRule
from analytics_api.domain.events import validate_event
from analytics_api.processing.aggregates import INSERT_BATCH_SQL, batch_parameters
from analytics_api.realtime.protocol import Alert, AlertState

from .factories import event_payload

NOW = datetime.now(UTC).replace(second=30, microsecond=0)


async def write(
    db: asyncpg.Pool, count: int, event_type: str, at: datetime, amount: str = "100"
) -> None:
    events = [
        validate_event(
            event_payload(event_type=event_type, occurred_at=at.isoformat(), amount_mad=amount)
        )
        for _ in range(count)
    ]
    await db.fetchrow(INSERT_BATCH_SQL, *batch_parameters(events))


def evaluator(db: asyncpg.Pool, changes: list[Alert], **kw: Any) -> AlertEvaluator:
    return AlertEvaluator(
        db,
        rules=kw.get(
            "rules", [CancellationRateRule(threshold=0.25, resolve_below=0.2, min_orders=30)]
        ),
        window_minutes=5,
        interval_s=60,
        fire_after=2,
        resolve_after=2,
        seconds_since_last_event=lambda: 1.0,
        on_change=changes.append,
    )


async def test_cancellation_spike_fires_once_survives_restart_and_resolves(
    db: asyncpg.Pool,
) -> None:
    await write(db, 40, "order_placed", NOW)
    await write(db, 20, "order_cancelled", NOW - timedelta(minutes=2))
    changes: list[Alert] = []
    first = evaluator(db, changes)
    await first.load_active()

    assert await first.evaluate_once(NOW) == []  # 1st breach: not yet
    fired = await first.evaluate_once(NOW)
    assert [(a.rule, a.state, a.value) for a in fired] == [
        ("cancellation_rate", AlertState.FIRING, 0.5)
    ]
    assert await first.evaluate_once(NOW) == []  # still firing: no duplicate
    assert changes == fired
    assert await db.fetchval("SELECT count(*) FROM alerts") == 1

    # A restarted API picks the open alert up instead of opening a second one.
    restarted = evaluator(db, changes)
    await restarted.load_active()
    assert [a.rule for a in restarted.active_alerts()] == ["cancellation_rate"]

    await write(db, 260, "order_placed", NOW)  # 20 / 300 = 6.7 %
    assert await restarted.evaluate_once(NOW) == []
    resolved = await restarted.evaluate_once(NOW)
    assert [(a.state, a.id) for a in resolved] == [(AlertState.RESOLVED, fired[0].id)]
    assert resolved[0].resolved_at == NOW
    assert restarted.active_alerts() == []
    assert await db.fetchval("SELECT state FROM alerts") == "resolved"


async def test_revenue_compares_complete_minutes_only(db: asyncpg.Pool) -> None:
    # Previous 5 complete minutes: 10 x 1000 MAD. Last 5 complete minutes: 2 x 1000 MAD.
    await write(db, 10, "order_paid", NOW - timedelta(minutes=8), amount="1000")
    await write(db, 2, "order_paid", NOW - timedelta(minutes=3), amount="1000")
    # The current, incomplete minute must be ignored by the revenue comparison.
    await write(db, 50, "order_paid", NOW, amount="1000")

    changes: list[Alert] = []
    ev = evaluator(
        db,
        changes,
        rules=[RevenueDropRule(threshold=0.5, resolve_below=0.3, min_baseline_mad=2000)],
    )
    inputs = await ev.read_inputs(NOW)
    assert (inputs.revenue_previous_mad, inputs.revenue_current_mad) == (
        Decimal(10_000),
        Decimal(2_000),
    )
    await ev.evaluate_once(NOW)
    fired = await ev.evaluate_once(NOW)
    assert fired[0].rule == "revenue_drop"
    assert fired[0].value == 0.8
