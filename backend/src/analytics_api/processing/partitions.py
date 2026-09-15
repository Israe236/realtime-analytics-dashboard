"""Daily partitions of the ``events`` table.

Partitions are named ``events_pYYYYMMDD`` and cover one UTC day. Two operations:

* :func:`ensure_daily_partitions` creates any missing partition in a window of days
  (by default: every day the API still accepts, plus a couple of days ahead so midnight never
  finds a missing partition).
* :func:`drop_expired_partitions` drops partitions whose whole day is older than the
  retention cutoff. Dropping a partition is a metadata operation: it takes milliseconds
  whatever the number of rows, and leaves no dead rows for vacuum to clean up.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, time, timedelta

import asyncpg

log = logging.getLogger(__name__)

_NAME = re.compile(r"^events_p(\d{8})$")

_LIST_SQL = """
SELECT c.relname
FROM pg_inherits i
JOIN pg_class c ON c.oid = i.inhrelid
WHERE i.inhparent = 'events'::regclass
"""


def partition_name(day: date) -> str:
    return f"events_p{day:%Y%m%d}"


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), tzinfo=UTC)
    return start, start + timedelta(days=1)


async def list_daily_partitions(pool: asyncpg.Pool) -> dict[date, str]:
    partitions: dict[date, str] = {}
    for row in await pool.fetch(_LIST_SQL):
        match = _NAME.match(row["relname"])
        if match:
            partitions[datetime.strptime(match.group(1), "%Y%m%d").date()] = row["relname"]
    return partitions


async def ensure_daily_partitions(
    pool: asyncpg.Pool, today: date, *, days_back: int, days_ahead: int
) -> list[str]:
    """Create missing partitions for [today - days_back, today + days_ahead]; return new names."""
    existing = await list_daily_partitions(pool)
    created: list[str] = []
    for offset in range(-days_back, days_ahead + 1):
        day = today + timedelta(days=offset)
        if day in existing:
            continue
        name = partition_name(day)
        start, end = day_bounds(day)
        try:
            # Names and bounds are generated here from a date, never from user input.
            await pool.execute(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF events "
                f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
            )
            created.append(name)
        except asyncpg.CheckViolationError:
            # The default partition already holds rows for that day (only possible if the
            # partition was missing when they arrived). Leave them there; retention still
            # removes them from the default partition when they expire.
            log.warning("cannot create %s: default partition has rows for that day", name)
    return created


async def drop_expired_partitions(pool: asyncpg.Pool, cutoff: datetime) -> list[str]:
    """Drop every daily partition whose entire day is older than ``cutoff``."""
    dropped: list[str] = []
    for day, name in sorted((await list_daily_partitions(pool)).items()):
        _, end = day_bounds(day)
        if end <= cutoff:
            await pool.execute(f"DROP TABLE IF EXISTS {name}")
            dropped.append(name)
    return dropped
