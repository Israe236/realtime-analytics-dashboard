"""Data retention: keep the tables that grow with traffic bounded.

Retention per table (defaults, all configurable):

* ``events`` (raw)            – 8 days, by dropping whole daily partitions
* ``dead_letter_events``      – 8 days
* ``agg_minute``              – 8 days  (dashboards read the last 2 hours)
* ``ingest_stats_minute``     – 8 days
* ``agg_hour``                – 400 days (small: ~100 rows per hour)

Why raw events are kept *longer* than the API's maximum event age (7 days): deduplication
relies on the primary key of ``events``. If an event could still be accepted after its raw
row was deleted, a retried copy would be inserted again and counted twice in the aggregates.
Keeping raw rows for longer than any accepted event can be old closes that gap.

``events`` is partitioned by day (see ``partitions.py``): expiring a day is a ``DROP TABLE``
of that day's partition. Row-by-row deletes are only needed for the default partition (rows
outside the daily partitions, normally none) and for the smaller unpartitioned tables. Those
deletes run in small batches so each statement holds locks briefly.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg

from analytics_api.processing.partitions import drop_expired_partitions, ensure_daily_partitions

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    raw_days: int = 8
    minute_days: int = 8
    hour_days: int = 400
    batch_size: int = 10_000
    # Partitions are kept ready for every day the API accepts, plus a margin ahead.
    partition_days_back: int = 7
    partition_days_ahead: int = 2


# (table, timestamp column, which policy field applies)
_ROW_DELETE_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("events_default", "occurred_at", "raw_days"),
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
                result = await self.run_once()
                if any(result.values()):
                    log.info("retention: %s", result)
            except Exception:
                log.exception("retention run failed; will retry next interval")
            await asyncio.sleep(self._interval_s)

    async def ensure_partitions(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(UTC)
        return await ensure_daily_partitions(
            self._pool,
            now.astimezone(UTC).date(),
            days_back=self._policy.partition_days_back,
            days_ahead=self._policy.partition_days_ahead,
        )

    async def run_once(self, now: datetime | None = None) -> dict[str, int]:
        """Prepare upcoming partitions, drop expired ones, delete expired rows elsewhere.

        Returns counts: partitions created and dropped, and rows deleted per table.
        """
        now = now or datetime.now(UTC)
        result = {"events_partitions_created": len(await self.ensure_partitions(now))}
        raw_cutoff = now - timedelta(days=self._policy.raw_days)
        result["events_partitions_dropped"] = len(
            await drop_expired_partitions(self._pool, raw_cutoff)
        )
        for table, column, field in _ROW_DELETE_TARGETS:
            cutoff = now - timedelta(days=getattr(self._policy, field))
            result[table] = await self._delete_before(table, column, cutoff)
        return result

    async def _delete_before(self, table: str, column: str, cutoff: datetime) -> int:
        # Table/column names come from the constant list above, never from input. ctid is only
        # unique within one table, which is why the default partition is targeted directly
        # rather than through the partitioned parent.
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
