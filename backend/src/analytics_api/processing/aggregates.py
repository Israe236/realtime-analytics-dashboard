"""Incremental aggregation.

The whole write path for a batch is ONE SQL statement built from data-modifying CTEs:

1. ``input``    – the batch, passed as one array per column and expanded with ``unnest``.
2. ``inserted`` – ``INSERT INTO events … ON CONFLICT (event_id, occurred_at) DO NOTHING
   RETURNING …`` (``events`` is partitioned by day, so its key must include ``occurred_at``).
   Only rows that were really new come out of RETURNING, so a duplicate event can never
   be counted twice in the aggregates.
3. ``minute_rollup`` / ``hour_rollup`` – group the *inserted* rows by bucket with
   ``GROUPING SETS`` (totals, per category, per city, per payment method) and add them to
   the existing bucket rows with ``ON CONFLICT DO UPDATE SET count = count + excluded``.
4. ``stats``    – per-minute accepted / duplicate counters for pipeline health.

Because it is a single statement, it is atomic: either the events *and* their aggregate
contributions are committed, or nothing is. Cost is proportional to the batch size, never
to the size of the ``events`` table.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from analytics_api.domain.events import OrderEvent

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("event_id", "uuid"),
    ("order_id", "uuid"),
    ("event_type", "text"),
    ("occurred_at", "timestamptz"),
    ("amount_mad", "numeric"),
    ("category", "text"),
    ("city", "text"),
    ("payment_method", "text"),
    ("customer_id", "text"),
)
_COLUMN_LIST = ", ".join(name for name, _ in _COLUMNS)
_UNNEST_ARGS = ", ".join(f"${i}::{pg_type}[]" for i, (_, pg_type) in enumerate(_COLUMNS, start=1))
_BATCH_SIZE_PARAM = f"${len(_COLUMNS) + 1}::bigint"


def _rollup_cte(name: str, table: str, unit: str) -> str:
    # Inside a grouping set, columns that are not part of the set come out as NULL;
    # GROUPING(col) = 0 tells us which dimension a result row belongs to.
    return f"""
{name} AS (
    INSERT INTO {table} AS agg (dimension, bucket, dim_value, event_type, event_count, amount_sum)
    SELECT
        CASE
            WHEN GROUPING(category) = 0 THEN 'category'
            WHEN GROUPING(city) = 0 THEN 'city'
            WHEN GROUPING(payment_method) = 0 THEN 'payment_method'
            ELSE 'all'
        END,
        bucket,
        COALESCE(category, city, payment_method, ''),
        event_type,
        count(*),
        sum(amount_mad)
    FROM (
        SELECT date_trunc('{unit}', occurred_at, 'UTC') AS bucket,
               event_type, amount_mad, category, city, payment_method
        FROM inserted
    ) AS rows
    GROUP BY GROUPING SETS (
        (bucket, event_type),
        (bucket, event_type, category),
        (bucket, event_type, city),
        (bucket, event_type, payment_method)
    )
    ON CONFLICT (dimension, bucket, dim_value, event_type) DO UPDATE
    SET event_count = agg.event_count + EXCLUDED.event_count,
        amount_sum  = agg.amount_sum + EXCLUDED.amount_sum
)"""


INSERT_BATCH_SQL = f"""
WITH input AS (
    SELECT * FROM unnest({_UNNEST_ARGS}) AS t({_COLUMN_LIST})
),
inserted AS (
    INSERT INTO events ({_COLUMN_LIST})
    SELECT {_COLUMN_LIST} FROM input
    ON CONFLICT (event_id, occurred_at) DO NOTHING
    RETURNING seq, event_type, occurred_at, amount_mad, category, city, payment_method
),
{_rollup_cte("minute_rollup", "agg_minute", "minute")},
{_rollup_cte("hour_rollup", "agg_hour", "hour")},
stats AS (
    INSERT INTO ingest_stats_minute AS s (bucket, accepted, duplicates)
    SELECT date_trunc('minute', now(), 'UTC'), c.n, {_BATCH_SIZE_PARAM} - c.n
    FROM (SELECT count(*) AS n FROM inserted) AS c
    ON CONFLICT (bucket) DO UPDATE
    SET accepted   = s.accepted + EXCLUDED.accepted,
        duplicates = s.duplicates + EXCLUDED.duplicates
)
SELECT count(*) AS inserted, max(seq) AS max_seq FROM inserted
"""


def batch_parameters(events: Sequence[OrderEvent]) -> list[Any]:
    """Column-oriented parameters for :data:`INSERT_BATCH_SQL` (one list per column)."""
    columns: list[list[Any]] = [[] for _ in _COLUMNS]
    for e in events:
        values = (
            e.event_id,
            e.order_id,
            e.event_type.value,
            e.occurred_at,
            e.amount_mad,
            e.category.value,
            e.city,
            e.payment_method.value,
            e.customer_id,
        )
        for column, value in zip(columns, values, strict=True):
            column.append(value)
    return [*columns, len(events)]


# Recomputes what the aggregate tables *should* contain from the raw events. Not used on the
# hot path — it exists to verify the incremental logic (tests) and to repair a table by hand.
REBUILD_EXPECTED_SQL = """
SELECT
    CASE
        WHEN GROUPING(category) = 0 THEN 'category'
        WHEN GROUPING(city) = 0 THEN 'city'
        WHEN GROUPING(payment_method) = 0 THEN 'payment_method'
        ELSE 'all'
    END AS dimension,
    bucket,
    COALESCE(category, city, payment_method, '') AS dim_value,
    event_type,
    count(*) AS event_count,
    sum(amount_mad) AS amount_sum
FROM (SELECT date_trunc($1, occurred_at, 'UTC') AS bucket, * FROM events) AS rows
GROUP BY GROUPING SETS (
    (bucket, event_type),
    (bucket, event_type, category),
    (bucket, event_type, city),
    (bucket, event_type, payment_method)
)
"""
