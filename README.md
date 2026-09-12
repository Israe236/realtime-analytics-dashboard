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

## Development (so far)

```bash
docker compose up -d postgres          # Postgres 17 on localhost:55432
uv sync                                # Python 3.13 workspace
uv run pytest                          # backend tests (need the postgres service)
uv run uvicorn --factory analytics_api.main:create_app --reload
```

Ingestion endpoints:

| Method | Path | Behaviour |
|---|---|---|
| `POST` | `/api/events/batch` | JSON array; each event validated separately. `202` after commit with `{accepted, rejected, errors[]}`. `400` unparseable body, `413` too large, `429` writer saturated (`Retry-After`), `503` commit timeout. |
| `POST` | `/api/events` | Single event; `422` with error details if invalid. |
| `GET` | `/api/health` | Database reachability and writer counters. |
