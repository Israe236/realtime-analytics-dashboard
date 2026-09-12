# Design decisions

This document explains *why* the system is built the way it is, in plain language. Each
section can be read on its own, without opening the code. Sections are added as milestones
are completed; "What went wrong" notes record real problems hit during development.

---

## 0. Repository and tooling

**One repo, several apps.** The backend, generator, benchmark and three frontends live
together so a single `docker compose up` can start everything and a single CI workflow can
test everything. Python projects share one `uv` workspace (one lock file, one set of dev
tools). The three frontends are independent npm projects, because Angular, Vite and Expo
each have their own build tooling and mixing them in one npm workspace tends to cause
dependency-hoisting problems (React Native is especially sensitive to that).

**Line endings.** The project is edited from Windows and WSL but built on Linux (Docker, CI).
`.gitattributes` forces LF so shell scripts and Dockerfiles don't break with `\r` characters.

**Postgres on host port 55432.** Many developer machines already run a Postgres on 5432.
Using an unusual host port avoids a confusing "connected to the wrong database" moment.

---

## 1. The event contract (validation)

**One Pydantic model is the source of truth** for what a valid event is. It checks types,
enums (event type, category, payment method), money (`> 0`, `≤ 1,000,000`, at most two
decimals, no NaN/Infinity), text lengths, and timestamps.

Choices worth being able to defend:

- **Money is `Decimal`, never `float`.** `0.1 + 0.2 != 0.3` in floating point. Sums of
  thousands of amounts must be exact, so the API parses amounts as decimals and the
  database stores `numeric(12,2)`. (Values are converted to floats only at the very end,
  when sent to the browser for display.)
- **Timestamps must include a timezone.** `2026-09-12T10:00:00` is ambiguous (Casablanca?
  UTC?), so it is rejected. Everything is converted to UTC on the way in.
- **Timestamps must be plausible.** More than 5 minutes in the future (a producer with a
  broken clock) or older than 7 days (a replay of old data) is rejected. Otherwise one bad
  clock could create "revenue" in a bucket next year.
- **Booleans are not numbers.** In Python `True == 1`, so lax parsing would accept
  `"amount_mad": true` as 1.00 MAD. A small pre-validator rejects that explicitly.
- **Unknown extra fields are ignored, not rejected.** A producer can start sending a new
  field before the API is upgraded, without its events being dead-lettered. The trade-off:
  a typo in an *optional* field name would go unnoticed (we have no optional fields today).
- **Error messages never echo the input back.** Dead-letter rows already keep the raw
  payload; API error responses only say *where* and *what* was wrong. This avoids
  reflecting arbitrary (possibly sensitive) data back to callers.

---

## 2. Database schema and indexing

| Table | What it holds | Key / index | Why |
|---|---|---|---|
| `events` | every accepted event | PK `event_id`; BRIN on `occurred_at` | PK doubles as the duplicate check. BRIN is tiny and good enough for rare time-range scans. |
| `agg_minute`, `agg_hour` | pre-computed counts and sums per time bucket | PK `(dimension, bucket, dim_value, event_type)` | Every dashboard read is "one dimension, recent buckets", which is exactly the leading part of this key. |
| `ingest_stats_minute` | accepted / duplicate / rejected per minute | PK `bucket` | Pipeline health without scanning raw tables. |
| `dead_letter_events` | rejected input + error | BRIN on `received_at` | Only inspected by humans. |
| `alerts` | alert history | index on `fired_at DESC`; **partial unique index** on `rule WHERE state = 'firing'` | The database itself guarantees at most one open alert per rule, even with bugs or several API instances. |

**Why so few indexes on `events`?** Every index is extra work on every insert. The live
dashboard never reads `events` — it reads the small aggregate tables — so `events` only
gets the primary key (needed for deduplication) and a BRIN index. A B-tree on
`occurred_at` would store one entry per row (a BRIN stores one per block range), and we
don't have a query that needs its precision. An index on `order_id` was left out on
purpose: nothing queries by order yet.

**BRIN in one sentence:** instead of indexing every row, it stores the min/max timestamp
for each block of ~128 pages; because events arrive roughly in time order, those ranges
barely overlap, so Postgres can skip almost all blocks for a time-range query.

**Text + CHECK instead of Postgres ENUM types.** Adding a value to a Postgres enum is a
migration with restrictions; changing a CHECK constraint is simpler. Validation already
happens in Pydantic, so the CHECK is only a last line of defence.

**One aggregate table with a `dimension` column** instead of `agg_minute_by_city`,
`agg_minute_by_category`, … : one upsert statement, one read pattern, and adding a new
dimension is a code change rather than a new table.

**Migrations** are plain numbered SQL files applied once each in a transaction, recorded in
`schema_migrations`. An advisory lock makes two API containers starting at the same time
wait for each other instead of both trying to create the same tables.

---

## 3. Why time-bucketed aggregates instead of live queries

The naive dashboard query is:

```sql
SELECT sum(amount_mad) FROM events
WHERE event_type = 'order_paid' AND occurred_at > now() - interval '60 minutes';
```

At 500 events/s that window holds ~1.8 million rows. Running it (plus top categories,
top cities, status counts…) for every dashboard refresh, several times per second, means
re-reading millions of rows continuously. Its cost grows with traffic and with the window.

Instead, every time a batch is written we **add** its contribution to per-minute and
per-hour buckets: "minute 10:42, category fashion, order_paid: +3 orders, +870.50 MAD".
The dashboard then reads at most 60 minute-buckets per dimension — a few thousand tiny rows
whatever the traffic. The work is done once, at write time, proportional to the batch size.

**Incremental, not periodic.** The update happens in the *same SQL statement* as the insert
of the raw events:

```
input (unnest arrays) → INSERT events ON CONFLICT DO NOTHING RETURNING new rows
                      → GROUP BY GROUPING SETS → UPSERT agg_minute (count = count + new)
                      → GROUP BY GROUPING SETS → UPSERT agg_hour
                      → UPSERT ingest_stats_minute
```

Three properties fall out of this design:

1. **Duplicates never double-count.** Only rows that `RETURNING` reports as newly inserted
   feed the aggregates. If a producer retries a batch, the retried events conflict on
   `event_id`, are not returned, and add nothing.
2. **Atomic.** One statement = one transaction. There is no moment where an event is stored
   but not yet counted, or counted but lost.
3. **Late events land in the right bucket.** Buckets come from `occurred_at`, not arrival
   time, and the upsert simply increments an old bucket.

`GROUPING SETS` computes totals, per-category, per-city and per-payment-method groups in a
single pass over the batch. `GROUPING(col) = 0` tells which set a result row came from.

**How we know it is correct:** a test writes 25 random batches (with duplicates inside and
across batches, spread over 3 hours) and checks that the aggregate tables are *exactly*
equal to a from-scratch `GROUP BY` over the raw `events` table.

**Trade-offs:** metrics are limited to what the buckets store (counts and sums — so
averages are fine, but exact medians/percentiles are not), and the finest resolution is one
minute. For an operational dashboard that is the right trade.

---

## 4. The write path: group commit and backpressure

**Requests do not write to the database.** They validate their events, hand the valid ones
to a single background *writer* task, and wait for a signal that the events are committed.
The writer takes everything waiting in the queue and writes it in one statement.

Why this shape:

- **Batching adapts to load by itself.** At low traffic, a batch is usually one request, so
  nothing waits. At high traffic, requests pile up while a write is in progress, so the next
  batch is bigger and the cost per event falls. There is no fixed "wait 50 ms to fill a
  batch" delay.
- **One writer means no lock fights.** If ten requests upserted the same aggregate rows
  concurrently, they would queue on row locks and could even deadlock. With one writer,
  aggregate updates never contend.
- **`events.seq` grows in commit order.** Because only one writer inserts, a higher `seq`
  always means "committed later". The WebSocket layer uses this to say "this snapshot
  includes everything up to seq N" (used to measure end-to-end latency).
- **The 202 means "stored".** The HTTP response is only sent after the commit, so a
  producer that got 202 knows the data is durable. `asyncio.shield` makes sure that if the
  client disconnects, the write still completes.

**Backpressure** means: when the system cannot keep up, tell the sender to slow down
rather than silently buffering. The writer queue has a hard limit (50,000 events by
default). When full, the API answers **429 Too Many Requests** with `Retry-After: 1`, and
the generator backs off. Memory stays bounded, and overload is visible instead of turning
into an out-of-memory crash minutes later. If a commit is slow (e.g. database restarting)
the request gets **503** after 10 s. Both are **safe to retry** because `event_id` makes
ingestion idempotent: at-least-once delivery from the producer + deduplication in the
database = each event counted once.

**Failures inside the writer:**

- *Transient* errors (connection dropped, DB restarting) → retry the same batch with
  exponential backoff (0.1 s, 0.2 s, … capped at 5 s). Meanwhile the queue fills and the API
  starts answering 429, so producers slow down automatically.
- *Permanent* errors (the database refuses the data itself, e.g. a CHECK constraint) →
  retrying would fail forever and block everything queued behind it (a "poison batch").
  The writer bisects the batch — tries each half separately — until it isolates the bad
  row(s), dead-letters them and commits the rest. That costs O(log n) extra statements and
  should never happen because Pydantic mirrors the constraints; it is a safety net.

**Known limitation:** the queue lives in the API process's memory. Events that are queued
but not yet committed are lost if the process is killed — but those requests never received
a 202, so a well-behaved producer resends them. Running several API processes would need
either one writer per process (and accepting some lock contention) or moving the queue
out of process (see "next steps" in the README).

**CPU note:** validation runs on the event loop, so while a large batch is being
validated that process serves nothing else. The benchmark (README) shows how far one
process goes; the fix at higher scale is more API processes behind a load balancer.

---

## 5. Dead letters: never crash, never lose the evidence

Anything the API cannot accept is stored in `dead_letter_events` with the reason and the
validation errors:

- `validation_error` — an item in a batch failed validation (other items are still accepted).
- `malformed_json` — the body is not JSON at all.
- `invalid_body` — valid JSON, but not an array.
- `database_rejected` — the safety net described above.

Details:

- **The API parses the request body itself** rather than letting FastAPI do it. FastAPI would
  answer 422 before our code runs, and the bad body would be gone.
- **Dead letters are written in the background, in batches.** They are diagnostics; the
  request should not wait for them. The buffer is bounded (10,000); if someone floods the
  API with garbage, extra items are counted as `dropped` instead of using unbounded memory.
- **NUL bytes are escaped** and payloads are truncated at 16,000 characters. What went wrong:
  a test sending `\x00` bytes showed that Postgres `text` cannot store NUL characters at all —
  the insert fails — so raw bodies are sanitised first.
- **Ordering subtlety:** for a batch, we submit the valid events to the writer *before*
  dead-lettering the invalid ones. If the writer is full we answer 429 and the producer resends
  the whole batch; recording dead letters first would store the same bad items twice.
