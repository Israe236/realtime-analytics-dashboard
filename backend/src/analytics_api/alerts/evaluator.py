"""Periodically evaluates alert rules and records/pushes state changes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

import asyncpg

from analytics_api.alerts.rules import AlertInputs, Rule, RuleTracker, Transition
from analytics_api.realtime.protocol import Alert

log = logging.getLogger(__name__)

# One pass over at most 2 × window minute buckets of the 'all' dimension.
_ORDER_INPUTS_SQL = """
SELECT
    coalesce(sum(event_count) FILTER (
        WHERE event_type = 'order_placed' AND bucket >= $2), 0) AS placed,
    coalesce(sum(event_count) FILTER (
        WHERE event_type = 'order_cancelled' AND bucket >= $2), 0) AS cancelled,
    coalesce(sum(amount_sum) FILTER (
        WHERE event_type = 'order_paid' AND bucket >= $3 AND bucket < $4), 0) AS revenue_current,
    coalesce(sum(amount_sum) FILTER (
        WHERE event_type = 'order_paid' AND bucket >= $1 AND bucket < $3), 0) AS revenue_previous
FROM agg_minute
WHERE dimension = 'all' AND bucket BETWEEN $1 AND $4
"""
_INGEST_INPUTS_SQL = """
SELECT coalesce(sum(accepted), 0) AS accepted, coalesce(sum(rejected), 0) AS rejected
FROM ingest_stats_minute
WHERE bucket >= $1
"""
_FIRE_SQL = """
INSERT INTO alerts (rule, severity, state, message, value, threshold, fired_at)
VALUES ($1, $2, 'firing', $3, $4, $5, $6)
ON CONFLICT (rule) WHERE state = 'firing' DO NOTHING
RETURNING *
"""
_RESOLVE_SQL = """
UPDATE alerts SET state = 'resolved', resolved_at = $2, message = $3, value = $4
WHERE rule = $1 AND state = 'firing'
RETURNING *
"""


def alert_from_row(row: asyncpg.Record) -> Alert:
    return Alert.model_validate(dict(row))


class AlertEvaluator:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        rules: Sequence[Rule],
        window_minutes: int,
        interval_s: float,
        fire_after: int,
        resolve_after: int,
        seconds_since_last_event: Callable[[], float | None],
        on_change: Callable[[Alert], None],
    ) -> None:
        self._pool = pool
        self._rules = list(rules)
        self._window_minutes = window_minutes
        self._interval_s = interval_s
        self._seconds_since_last_event = seconds_since_last_event
        self._on_change = on_change
        self._trackers = {
            rule.name: RuleTracker(fire_after=fire_after, resolve_after=resolve_after)
            for rule in self._rules
        }
        self._active: dict[str, Alert] = {}
        self._task: asyncio.Task[None] | None = None

    def active_alerts(self) -> list[Alert]:
        return sorted(self._active.values(), key=lambda a: a.fired_at)

    async def load_active(self) -> None:
        """Resume firing alerts after a restart instead of opening duplicates."""
        rows = await self._pool.fetch("SELECT * FROM alerts WHERE state = 'firing'")
        for row in rows:
            alert = alert_from_row(row)
            if alert.rule in self._trackers:
                self._trackers[alert.rule].firing = True
                self._active[alert.rule] = alert

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="alert-evaluator")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval_s)
            try:
                await self.evaluate_once()
            except Exception:
                log.exception("alert evaluation failed; will retry")

    async def read_inputs(self, now: datetime) -> AlertInputs:
        minute = now.replace(second=0, microsecond=0)
        window = timedelta(minutes=self._window_minutes)
        recent_start = minute - window + timedelta(minutes=1)  # includes the current minute
        current_start = minute - window  # complete minutes only
        previous_start = current_start - window
        async with self._pool.acquire() as conn:
            orders = await conn.fetchrow(
                _ORDER_INPUTS_SQL, previous_start, recent_start, current_start, minute
            )
            ingest = await conn.fetchrow(_INGEST_INPUTS_SQL, recent_start)
        assert orders is not None and ingest is not None
        return AlertInputs(
            window_minutes=self._window_minutes,
            placed=int(orders["placed"]),
            cancelled=int(orders["cancelled"]),
            accepted=int(ingest["accepted"]),
            rejected=int(ingest["rejected"]),
            revenue_current_mad=orders["revenue_current"],
            revenue_previous_mad=orders["revenue_previous"],
            seconds_since_last_event=self._seconds_since_last_event(),
        )

    async def evaluate_once(self, now: datetime | None = None) -> list[Alert]:
        """Evaluate every rule once; return the alerts whose state changed."""
        now = now or datetime.now(UTC)
        inputs = await self.read_inputs(now)
        changed: list[Alert] = []
        for rule in self._rules:
            evaluation = rule.evaluate(inputs)
            transition = self._trackers[rule.name].step(evaluation)
            if transition is Transition.FIRE:
                row = await self._pool.fetchrow(
                    _FIRE_SQL,
                    rule.name,
                    rule.severity.value,
                    evaluation.message,
                    evaluation.value,
                    rule.threshold,
                    now,
                )
                if row is None:  # another instance already opened it
                    row = await self._pool.fetchrow(
                        "SELECT * FROM alerts WHERE rule = $1 AND state = 'firing'", rule.name
                    )
                if row is not None:
                    alert = alert_from_row(row)
                    self._active[rule.name] = alert
                    changed.append(alert)
            elif transition is Transition.RESOLVE:
                row = await self._pool.fetchrow(
                    _RESOLVE_SQL, rule.name, now, evaluation.message, evaluation.value
                )
                self._active.pop(rule.name, None)
                if row is not None:
                    changed.append(alert_from_row(row))
        for alert in changed:
            log.warning("alert %s %s: %s", alert.rule, alert.state, alert.message)
            self._on_change(alert)
        return changed
