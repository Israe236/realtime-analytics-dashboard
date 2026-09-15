-- Partition raw events by day (on occurred_at) so retention can drop a whole day at once
-- instead of deleting millions of rows one by one.
--
-- A partitioned table's primary key must include the partition column, so the dedupe key
-- becomes (event_id, occurred_at). A retried event carries the same occurred_at, so retries
-- are still recognised as duplicates.

ALTER TABLE events RENAME TO events_unpartitioned;
ALTER TABLE events_unpartitioned RENAME CONSTRAINT events_pkey TO events_unpartitioned_pkey;
ALTER INDEX events_occurred_at_brin RENAME TO events_unpartitioned_occurred_at_brin;

CREATE TABLE events (
    seq            bigint GENERATED ALWAYS AS IDENTITY,
    event_id       uuid NOT NULL,
    order_id       uuid NOT NULL,
    event_type     text NOT NULL CHECK (event_type IN
                       ('order_placed', 'order_paid', 'order_shipped', 'order_cancelled')),
    occurred_at    timestamptz NOT NULL,
    ingested_at    timestamptz NOT NULL DEFAULT now(),
    amount_mad     numeric(12, 2) NOT NULL CHECK (amount_mad > 0),
    category       text NOT NULL,
    city           text NOT NULL,
    payment_method text NOT NULL,
    customer_id    text NOT NULL,
    PRIMARY KEY (event_id, occurred_at)
) PARTITION BY RANGE (occurred_at);

-- Created on the parent, so every partition gets its own small BRIN index.
CREATE INDEX events_occurred_at_brin ON events USING brin (occurred_at);

-- Safety net for timestamps outside the daily partitions. Normal traffic never lands here:
-- the application keeps partitions ready for every day the API accepts.
CREATE TABLE events_default PARTITION OF events DEFAULT;

-- One partition per UTC day, covering existing data and the days the API currently accepts.
DO $$
DECLARE
    today     date := (now() AT TIME ZONE 'UTC')::date;
    first_day date;
    day       date;
BEGIN
    SELECT LEAST(COALESCE(min((occurred_at AT TIME ZONE 'UTC')::date), today), today - 7)
      INTO first_day
      FROM events_unpartitioned;
    day := first_day;
    WHILE day <= today + 2 LOOP
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF events FOR VALUES FROM (%L) TO (%L)',
            'events_p' || to_char(day, 'YYYYMMDD'),
            day::timestamp AT TIME ZONE 'UTC',
            (day + 1)::timestamp AT TIME ZONE 'UTC'
        );
        day := day + 1;
    END LOOP;
END $$;

INSERT INTO events OVERRIDING SYSTEM VALUE
SELECT seq, event_id, order_id, event_type, occurred_at, ingested_at,
       amount_mad, category, city, payment_method, customer_id
FROM events_unpartitioned;

-- Continue numbering after the copied rows (the WebSocket watermark relies on seq growing).
SELECT setval(
    pg_get_serial_sequence('events', 'seq'),
    COALESCE((SELECT max(seq) FROM events), 0) + 1,
    false
);

DROP TABLE events_unpartitioned;
