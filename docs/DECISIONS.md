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
- **NUL bytes are escaped** and payloads are truncated at 16,000 characters. Postgres `text`
  cannot store the NUL character (`\x00`) at all — an insert containing one fails — so a
  garbage body could otherwise break the very table meant to capture garbage. A test posts
  `\x00` bytes to prove the dead-letter path survives it.
- **Ordering subtlety:** for a batch, we submit the valid events to the writer *before*
  dead-lettering the invalid ones. If the writer is full we answer 429 and the producer resends
  the whole batch; recording dead letters first would store the same bad items twice.

---

## 6. The event generator

The generator is a small asyncio program that behaves like a busy shop's backend.

**Traffic shape.** The target rate (e.g. 50 events/s) is multiplied by a *daily curve*: a
low night, a late-morning bump, a lunch peak and a strong evening peak. The curve is
normalised so its 24-hour average is exactly 1, which keeps "events per second" meaning
the daily average. A simulated clock runs faster than real time so a demo shows the whole
day: by default ×12, one simulated day lasts two real hours.

*Why ×12 and not faster:* at ×60 the evening-to-night decline happens within a few real
minutes, which the "revenue drop" alert (last 5 minutes vs previous 5 minutes) would
correctly report as a drop — every day. At ×12, five real minutes are one simulated hour,
so the natural curve changes slowly enough that alerts fire on real anomalies, not on
bedtime.

**Orders, not just events.** Each new order emits `order_placed` and schedules its future
events on a min-heap (a priority queue sorted by due time):
prepaid orders go placed → paid → shipped; cash-on-delivery orders go placed → shipped →
paid (the courier collects the money); some orders are cancelled instead, and COD orders
are cancelled more often. Because the target is expressed in events/s, the generator
converts it into new orders/s by dividing by the expected number of events per order
(≈ 2.93); a test checks that estimate against a simulation of 5,000 orders.

**Amounts** are log-normal per category (a median and a spread): most fashion orders
are a few hundred MAD, a few are expensive; electronics have a much higher median. That is
how real basket values look — many small, a long tail of large ones.

**Bursts** (flash sales, ×3–5 traffic for 20–45 s) arrive as a Poisson process: the waiting
time between bursts is drawn from an exponential distribution, which is the standard model
for independent random arrivals.

**Anomalies** exist to exercise alerting. Every ~7 minutes one of these runs, for long
enough to dominate a 5-minute alert window:

| Anomaly | Effect | Alert it should trigger |
|---|---|---|
| `cancellation_spike` (4 min) | new orders cancelled at 55 % | cancellation rate |
| `revenue_drop` (7 min) | order rate falls to 10 % | revenue drop |
| `bad_producer` (2.5 min) | 25 % of events malformed | dead-letter rate |

**Malformed events** (1 % normally) come from a list of concrete corruptions: missing field,
negative amount, text instead of a number, unknown category, naive timestamp, bad UUID,
empty city, a string instead of an object. A test runs every corruption through the
*backend's* Pydantic model and asserts it is rejected — so the generator and the API
cannot silently drift apart.

**Sending and backpressure (client side).**

- One request at a time. If the API is slow, the next batch simply grows (up to 2,000
  events): the generator slows down exactly as much as the API needs.
- `429`/`503` → wait for `Retry-After`, then resend *the same bytes*. That is safe because
  event IDs make retries idempotent.
- Network errors → exponential backoff **with full jitter**: wait a random time between
  0 and `min(10 s, 0.2 s × 2^attempt)`. Without the randomness, many clients that failed at
  the same moment would all retry at the same moment and knock the server over again
  (the "thundering herd").
- Other 4xx (e.g. 413) → drop and log; resending can never succeed.
- The outgoing buffer is bounded (200,000 events). If the API is down for a long time the
  generator drops the *oldest* events and counts them — for a live dashboard, fresh data
  is worth more than a complete backlog, and the generator must not run out of memory.

---

## 7. Continuous integration

GitHub Actions runs on every push and pull request. The Python job starts a real
PostgreSQL 17 as a *service container* (the same image as docker compose), then runs exactly
the commands used locally: `uv sync --frozen` (fails if the lock file is stale), `ruff check`,
`ruff format --check`, `mypy --strict`, and `pytest`. Database tests are not mocked: the
aggregation logic *is* SQL, so mocking the database would test nothing.
`concurrency` cancels an older run on the same branch when a newer commit is pushed.

---

## 8. Why WebSockets instead of polling

**Polling** means every browser asks "anything new?" every N seconds. It has two bad knobs:

- Poll often (every 500 ms) → latency is OK, but 1,000 open dashboards make 2,000 HTTP
  requests per second, each running the metric queries, *even when nothing changed*.
- Poll rarely (every 10 s) → cheap, but the "live" dashboard is up to 10 s stale.

**A WebSocket** is one long-lived connection per client. The server pushes when something
changes, so latency is bounded by how fast the server reacts, and cost does not grow with
how impatient the clients are. We still need a few HTTP endpoints (`/api/metrics/snapshot`,
`/api/alerts`) for the first page load and history, but the live stream is push.

**Why not Server-Sent Events (SSE)?** SSE would also work — our stream is one-directional.
WebSockets were chosen because the requirement asked for them, every frontend stack has
first-class support (including React Native), and they leave room for client → server
messages later (e.g. "subscribe to one city only").

**Cost model — the key point.** Queries do *not* scale with clients:

1. A committed batch sets a "dirty" flag (no I/O, nanoseconds).
2. The broadcaster wakes, runs **one** snapshot query set, serialises the JSON **once**.
3. The same string is handed to every client.

So 1 client or 1,000 clients cause the same database load. The broadcaster is throttled:
at most one snapshot every 250 ms, however many batches commit (at 5,000 events/s there may
be dozens of commits per second; clients don't need dozens of redraws). With no traffic it
still refreshes every 2 s so rolling windows keep moving and a stalled pipeline becomes
visible.

**Snapshot, not deltas.** Each message is a complete picture (KPIs, 60 minute-buckets,
rankings). A snapshot is a few KB, so sending the whole thing is affordable and makes
everything simpler: a client that missed messages, reconnected, or skipped some because it
was slow is correct again after the next one. With deltas, one lost message leaves a
client wrong until it resynchronises.

**Consistent numbers.** A snapshot runs its four queries inside one read-only
`REPEATABLE READ` transaction, so they all see the database at the same instant; otherwise
the chart could include a batch that the KPI cards don't.

**The `through_seq` watermark.** Before building a snapshot, the broadcaster reads the
writer's last committed `seq`. Everything up to that number was committed before the
snapshot transaction started, so the snapshot is guaranteed to include it. The benchmark
uses this to measure end-to-end latency precisely ("event committed with seq 81,234 was
first visible to a client at time T").

---

## 9. Slow clients, disconnections and backpressure on the WebSocket side

A WebSocket server has its own backpressure problem: if one client is on a bad mobile
connection and reads slowly, messages pile up for it. A naive `for client in clients:
await client.send(msg)` loop lets that one client delay *everyone*, and buffering without
limit eventually exhausts memory.

What we do instead:

- **Publishing never waits for a client.** Each connection has its own mailbox and its
  own sender task. The broadcaster just drops the message in every mailbox and moves on.
- **Conflation ("latest wins") for snapshots and the event feed.** A mailbox holds at most
  one pending snapshot. If a new one arrives before the old one was sent, the old one is
  replaced. A slow client simply receives fewer, newer snapshots — which is exactly right,
  because each snapshot is complete. Memory per client stays constant.
- **A small reliable queue** for messages that must not be skipped (`hello`, `alert`,
  `ping`). They are sent before any pending snapshot. If this queue overflows (200
  messages), the client is hopelessly behind and is disconnected with close code 1008; it
  will reconnect and get a fresh `hello` with the current alerts.
- **Send timeout (5 s).** A client whose TCP connection died silently (laptop lid closed,
  phone lost signal) can make a send hang. After 5 s the connection is dropped.
- **Noticing disconnects.** Clients send nothing we need, but a second task keeps reading
  from the socket, because reading is how a close (or an abrupt TCP reset) is detected. When
  either the reader or the sender finishes, the other is cancelled and the client is
  removed from the hub. A test aborts a client's TCP connection without a close handshake
  and checks the other client keeps receiving updates.
- **Capacity limit.** Beyond `APP_WS_MAX_CLIENTS` (1,000) new connections are accepted and
  immediately closed with code **1013 "try again later"**, so clients back off instead of
  the server degrading for everyone.
- **Heartbeat.** The server sends `{"type": "ping"}` every 15 s. Browsers don't expose
  WebSocket protocol-level pings to JavaScript, so this application-level message lets a
  client detect a dead connection ("no message for 30 s → reconnect").

**Client side** (implemented in the frontends): reconnect with exponential backoff and full
jitter (the same idea as the generator, for the same thundering-herd reason — if the API
restarts, 1,000 dashboards must not reconnect in the same millisecond).

---

## 10. Alerting

Four rules run every 5 seconds against the aggregate tables (never the raw events):

| Rule | Fires when | Resolves when | Minimum data | Severity |
|---|---|---|---|---|
| `cancellation_rate` | cancelled / placed ≥ 25 % over the last 5 min | < 20 % | 30 orders | warning |
| `revenue_drop` | revenue of the last 5 complete minutes is ≥ 50 % below the 5 minutes before | drop < 40 % | 2,000 MAD baseline | critical |
| `dead_letter_rate` | rejected / received ≥ 5 % over the last 5 min | < 4 % | 100 events | warning |
| `ingestion_stalled` | no batch committed for ≥ 30 s | < 30 s | at least one event since start | critical |

All thresholds are environment variables (`APP_ALERT_*`).

Design choices:

- **Rules are pure functions.** A rule gets numbers and returns "value, breached,
  recovered, message". No database, no clock. That makes every edge case a one-line unit
  test (zero orders, tiny baseline, exactly at the threshold, revenue *growth*).
- **Minimum volume.** At 3 orders, one cancellation is 33 % — statistically meaningless.
  Below the minimum, a rule returns "no data", which leaves the alert state unchanged.
- **Complete minutes for comparisons.** The current minute is still filling up; comparing
  "last 5 minutes including 12 seconds of the current one" to "5 full minutes before" would
  always look like a drop. The revenue rule therefore uses complete minutes only (a test
  puts a large amount in the current minute and checks it is ignored).
- **Hysteresis.** Fire at 25 %, resolve only under 20 % (resolve = threshold × 0.8). A value
  wobbling around 25 % does not produce a stream of fire/resolve/fire notifications.
- **Consecutive evaluations.** A condition must hold for 2 evaluations in a row (~10 s)
  before an alert fires or resolves — like Prometheus' `for:` clause. One noisy sample does
  not page anyone.
- **The database guarantees one open alert per rule** via a partial unique index
  (`UNIQUE (rule) WHERE state = 'firing'`). Opening an alert is `INSERT … ON CONFLICT DO
  NOTHING`, so even two API instances evaluating at the same moment cannot open duplicates.
- **Restarts don't duplicate alerts.** On start-up the evaluator loads firing alerts from
  the table and resumes tracking them.
- **Delivery.** State changes are pushed over the WebSocket through the reliable (never
  conflated) queue. A client that connects later receives the currently open alerts in its
  `hello` message, and `GET /api/alerts` returns the history.

Limitation: thresholds are static. A shop whose normal cancellation rate is 30 % would need
different settings; per-segment baselines or anomaly detection on seasonality would be the
next step.
