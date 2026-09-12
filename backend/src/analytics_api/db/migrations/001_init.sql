-- Raw events. Append-mostly; the primary key doubles as the idempotency check.
CREATE TABLE events (
    seq            bigint GENERATED ALWAYS AS IDENTITY,
    event_id       uuid PRIMARY KEY,
    order_id       uuid NOT NULL,
    event_type     text NOT NULL CHECK (event_type IN
                       ('order_placed', 'order_paid', 'order_shipped', 'order_cancelled')),
    occurred_at    timestamptz NOT NULL,
    ingested_at    timestamptz NOT NULL DEFAULT now(),
    amount_mad     numeric(12, 2) NOT NULL CHECK (amount_mad > 0),
    category       text NOT NULL,
    city           text NOT NULL,
    payment_method text NOT NULL,
    customer_id    text NOT NULL
);

-- BRIN: a few kilobytes for millions of rows, because rows arrive roughly in time order.
-- Used only for ad-hoc/range queries (e.g. rebuilding aggregates), not by the hot path.
CREATE INDEX events_occurred_at_brin ON events USING brin (occurred_at);

-- Anything we could not accept: invalid events and unparseable request bodies.
CREATE TABLE dead_letter_events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    received_at timestamptz NOT NULL DEFAULT now(),
    reason      text NOT NULL,          -- validation_error | malformed_json | invalid_body
    errors      jsonb NOT NULL DEFAULT '[]'::jsonb,
    payload     text NOT NULL           -- raw text (possibly truncated), may not be valid JSON
);
CREATE INDEX dead_letter_received_at_brin ON dead_letter_events USING brin (received_at);

-- Time-bucketed aggregates, maintained incrementally by the writer.
-- One row per (dimension, bucket, dimension value, event type):
--   dimension = 'all'            -> dim_value = ''       (global totals)
--   dimension = 'category'       -> dim_value = 'fashion', ...
--   dimension = 'city'           -> dim_value = 'Rabat', ...
--   dimension = 'payment_method' -> dim_value = 'card', ...
-- The key starts with (dimension, bucket) because every read is
-- "one dimension, a recent range of buckets".
CREATE TABLE agg_minute (
    dimension   text NOT NULL,
    bucket      timestamptz NOT NULL,
    dim_value   text NOT NULL,
    event_type  text NOT NULL,
    event_count bigint NOT NULL,
    amount_sum  numeric(18, 2) NOT NULL,
    PRIMARY KEY (dimension, bucket, dim_value, event_type)
);

CREATE TABLE agg_hour (LIKE agg_minute INCLUDING ALL);

-- Pipeline health per minute of *ingestion* time (not event time).
CREATE TABLE ingest_stats_minute (
    bucket     timestamptz PRIMARY KEY,
    accepted   bigint NOT NULL DEFAULT 0,
    duplicates bigint NOT NULL DEFAULT 0,
    rejected   bigint NOT NULL DEFAULT 0
);

CREATE TABLE alerts (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule        text NOT NULL,
    severity    text NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    state       text NOT NULL CHECK (state IN ('firing', 'resolved')),
    message     text NOT NULL,
    value       double precision,
    threshold   double precision NOT NULL,
    fired_at    timestamptz NOT NULL,
    resolved_at timestamptz
);
CREATE INDEX alerts_fired_at_idx ON alerts (fired_at DESC);
-- The database itself guarantees at most one open alert per rule.
CREATE UNIQUE INDEX alerts_one_firing_per_rule ON alerts (rule) WHERE state = 'firing';
