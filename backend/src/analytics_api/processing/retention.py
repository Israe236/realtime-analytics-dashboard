"""Data retention: keep the tables that grow with traffic bounded.

Retention per table (defaults, all configurable):

* ``events`` (raw)            – 8 days
* ``dead_letter_events``      – 8 days
* ``agg_minute``              – 8 days  (dashboards read the last 2 hours)
* ``ingest_stats_minute``     – 8 days
* ``agg_hour``                – 400 days (small: ~100 rows per hour)

Why raw events are kept *longer* than the API's maximum event age (7 days): deduplication
relies on the ``event_id`` primary key. If an event could still be accepted after its raw
row was deleted, a retried copy would be inserted again and counted twice in the aggregates.
Keeping raw rows for longer than any accepted event can be old closes that gap.

Deletes run in small batches so each statement holds locks briefly and never competes with
ingestion for long; the BRIN index on ``occurred_at`` makes finding old rows cheap because
they sit at the start of the table.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    raw_days: int = 8
    minute_days: int = 8
    hour_days: int = 400
    batch_size: int = 10_000


# (table, timestamp column, which policy field applies)
_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("events", "occurred_at", "raw_days"),
    ("dead_letter_events", "received_at", "raw_days"),
    ("agg_minute", "bucket", "minute_days"),
    ("ingest_stats_minute", "bucket", "minute_days"),
    ("agg_hour", "bucket", "hour_days"),
)


class RetentionJob:
    def __init__(self, pool: asyncpg.Pool, policy: RetentionPolicy, *, interval_s: float) -> None:
        self._pool = pool
        self._policy = policy
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="retention")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            try:
                deleted = await self.run_once()
                if any(deleted.values()):
                    log.info("retention deleted %s", deleted)
            except Exception:
                log.exception("retention run failed; will retry next interval")
            await asyncio.sleep(self._interval_s)

    async def run_once(self, now: datetime | None = None) -> dict[str, int]:
        """Delete expired rows from every table; return the number deleted per table."""
        now = now or datetime.now(UTC)
        deleted: dict[str, int] = {}
        for table, column, field in _TARGETS:
            cutoff = now - timedelta(days=getattr(self._policy, field))
            deleted[table] = await self._delete_before(table, column, cutoff)
        return deleted

    async def _delete_before(self, table: str, column: str, cutoff: datetime) -> int:
        # Table/column names come from the constant list above, never from input.
        sql = (
            f"DELETE FROM {table} WHERE ctid IN ("
            f"SELECT ctid FROM {table} WHERE {column} < $1 LIMIT $2)"
        )
        total = 0
        while True:
            status = await self._pool.execute(sql, cutoff, self._policy.batch_size)
            count = int(status.split()[-1])  # "DELETE 1234"
            total += count
            if count < self._policy.batch_size:
                return total
            await asyncio.sleep(0)  # let ingestion interleave between batches
