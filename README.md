# realtime-analytics-dashboard

Real-time analytics pipeline for e-commerce order events (MAD amounts, Moroccan cities) with
live dashboards in React, Angular and React Native.

> 🚧 Work in progress — see [PLAN.md](PLAN.md) for milestones and
> [docs/DECISIONS.md](docs/DECISIONS.md) for design rationale.

## Status

| Milestone | State |
|---|---|
| M0 Plan & repo skeleton | ✅ |
| M1 Event contract, schema, migrations | ✅ |
| M2 Ingestion API, group-commit writer, incremental aggregates, dead letters | ✅ |
| M3 Event generator (daily curve, bursts, anomalies, malformed events, backoff) | ✅ |
| M4 CI: ruff, mypy --strict, pytest against Postgres | ✅ |
| M5 Metric snapshots + WebSocket fan-out (conflation, slow-client handling) | ✅ |
| M6 Alerting rules with hysteresis, pushed over WebSocket | ✅ |

## Development (so far)

```bash
docker compose up -d postgres          # Postgres 17 on localhost:55432
uv sync                                # Python 3.13 workspace
uv run pytest                          # backend tests (need the postgres service)
uv run uvicorn --factory analytics_api.main:create_app --reload
GEN_EVENTS_PER_SECOND=100 uv run python -m event_generator   # synthetic traffic
```

Generator settings (environment variables): `GEN_API_URL`, `GEN_EVENTS_PER_SECOND` (daily
average), `GEN_TIME_SCALE` (simulated clock speed, default ×12), `GEN_MALFORMED_RATIO`,
`GEN_BURSTS`, `GEN_ANOMALIES`, `GEN_ANOMALY_INTERVAL_S`, `GEN_SEED`.

Ingestion endpoints:

| Method | Path | Behaviour |
|---|---|---|
| `POST` | `/api/events/batch` | JSON array; each event validated separately. `202` after commit with `{accepted, rejected, errors[]}`. `400` unparseable body, `413` too large, `429` writer saturated (`Retry-After`), `503` commit timeout. |
| `POST` | `/api/events` | Single event; `422` with error details if invalid. |
| `GET` | `/api/health` | Database reachability and writer counters. |
| `GET` | `/api/metrics/snapshot` | Latest metrics snapshot (same JSON as the WebSocket message). |
| `GET` | `/api/alerts?limit=50` | Alert history, newest first. |
| `WS` | `/ws/live` | Server → client stream: `hello` (open alerts, recent events), `snapshot` (≤ 4/s), `events` (sampled feed), `alert`, `ping` (every 15 s). |

## Alerting rules

| Rule | Fires when | Resolves when | Minimum data |
|---|---|---|---|
| `cancellation_rate` | cancelled / placed ≥ 25 % (last 5 min) | < 20 % | 30 orders |
| `revenue_drop` | last 5 complete minutes ≥ 50 % below the previous 5 | drop < 40 % | 2,000 MAD baseline |
| `dead_letter_rate` | rejected / received ≥ 5 % (last 5 min) | < 4 % | 100 events |
| `ingestion_stalled` | nothing committed for ≥ 30 s | < 30 s | one event since start |

A condition must hold for 2 consecutive evaluations (every 5 s) to fire or resolve. All
thresholds are configurable with `APP_ALERT_*` environment variables.
