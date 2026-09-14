"""Load test: sustained ingest throughput and end-to-end latency.

What is measured
----------------
* **Accepted events/s** — events the API acknowledged with 202 (i.e. committed), divided
  by the measured duration.
* **Ack latency** — HTTP request start → 202 received, per batch.
* **End-to-end latency** — event creation (its ``occurred_at``) → the first WebSocket
  ``snapshot`` whose ``through_seq`` covers that batch arrives at a subscribed client.
  The 202 response carries ``committed_through_seq``; a snapshot with
  ``through_seq >= committed_through_seq`` is guaranteed to include the batch, because the
  broadcaster reads the watermark before it queries the aggregates.

Both clocks are ``time.time()`` on the benchmark host, so no cross-machine clock skew is
involved. The WebSocket client runs in the same process as the load, so e2e latency
includes delivery to a real client, not just the server-side commit.

Usage::

    uv run python -m bench --duration 60 --rate 1000      # fixed offered load
    uv run python -m bench --duration 60 --rate 0         # as fast as possible
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import random
import statistics
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import orjson
from websockets.asyncio.client import connect

CATEGORIES = ("electronics", "fashion", "home", "beauty", "grocery", "sports", "books", "toys")
CITIES = ("Casablanca", "Rabat", "Marrakech", "Fès", "Tanger", "Agadir")
METHODS = ("card", "cash_on_delivery", "wallet", "bank_transfer")
TYPES = ("order_placed", "order_paid", "order_shipped", "order_cancelled")


def make_batch(size: int, rng: random.Random) -> tuple[bytes, float]:
    """A batch of valid events, all created 'now'. Returns (body, creation time)."""
    created = time.time()
    occurred_at = datetime.fromtimestamp(created, UTC).isoformat()
    events = [
        {
            "event_id": str(uuid.uuid4()),
            "order_id": str(uuid.uuid4()),
            "event_type": rng.choice(TYPES),
            "occurred_at": occurred_at,
            "amount_mad": f"{rng.uniform(20, 3000):.2f}",
            "category": rng.choice(CATEGORIES),
            "city": rng.choice(CITIES),
            "payment_method": rng.choice(METHODS),
            "customer_id": f"bench-{rng.randint(1, 100_000)}",
        }
        for _ in range(size)
    ]
    return orjson.dumps(events), created


@dataclass
class Stats:
    batches: int = 0
    accepted: int = 0
    rejected_429: int = 0
    other_errors: int = 0
    ack_ms: list[float] = field(default_factory=list)
    # (committed_through_seq, creation time) waiting for a covering snapshot
    pending: list[tuple[int, float]] = field(default_factory=list)
    e2e_ms: list[float] = field(default_factory=list)
    snapshots: int = 0


class TokenBucket:
    """Paces the offered load to `rate` events/s (rate 0 = unlimited)."""

    def __init__(self, rate: float) -> None:
        self.rate = rate
        self.next_time = time.perf_counter()

    async def take(self, events: int) -> None:
        if self.rate <= 0:
            return
        self.next_time = max(self.next_time, time.perf_counter()) + events / self.rate
        delay = self.next_time - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)


async def sender(
    client: httpx.AsyncClient,
    bucket: TokenBucket,
    stats: Stats,
    batch_size: int,
    deadline: float,
    rng: random.Random,
) -> None:
    while time.perf_counter() < deadline:
        await bucket.take(batch_size)
        body, created = make_batch(batch_size, rng)
        started = time.perf_counter()
        try:
            response = await client.post(
                "/api/events/batch", content=body, headers={"content-type": "application/json"}
            )
        except httpx.HTTPError:
            stats.other_errors += 1
            continue
        if response.status_code == 202:
            payload = response.json()
            stats.batches += 1
            stats.accepted += int(payload["accepted"])
            stats.ack_ms.append((time.perf_counter() - started) * 1000)
            stats.pending.append((int(payload["committed_through_seq"]), created))
        elif response.status_code == 429:
            stats.rejected_429 += 1
            await asyncio.sleep(float(response.headers.get("retry-after", "1")))
        else:
            stats.other_errors += 1


async def subscriber(ws_url: str, stats: Stats, stop: asyncio.Event) -> None:
    async with connect(ws_url, max_size=None) as ws:
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1)
            except TimeoutError:
                continue
            received = time.time()
            message = orjson.loads(raw)
            if message.get("type") != "snapshot":
                continue
            stats.snapshots += 1
            through = int(message["through_seq"])
            still_pending = []
            for seq, created in stats.pending:
                if seq <= through:
                    stats.e2e_ms.append((received - created) * 1000)
                else:
                    still_pending.append((seq, created))
            stats.pending = still_pending


def percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None, "max": None}
    if len(values) < 2:
        return {"p50": values[0], "p95": values[0], "p99": values[0], "max": values[0]}
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return {
        "p50": round(cuts[49], 1),
        "p95": round(cuts[94], 1),
        "p99": round(cuts[98], 1),
        "max": round(max(values), 1),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    stats = Stats()
    stop = asyncio.Event()
    rng = random.Random(args.seed)
    limits = httpx.Limits(
        max_connections=args.concurrency, max_keepalive_connections=args.concurrency
    )
    async with httpx.AsyncClient(base_url=args.api, timeout=30, limits=limits) as client:
        (await client.get("/api/health")).raise_for_status()
        ws_task = asyncio.create_task(subscriber(args.ws, stats, stop))
        await asyncio.sleep(1)  # let the subscriber connect and receive hello
        bucket = TokenBucket(args.rate)
        started = time.perf_counter()
        deadline = started + args.duration
        await asyncio.gather(
            *(
                sender(client, bucket, stats, args.batch_size, deadline, rng)
                for _ in range(args.concurrency)
            )
        )
        elapsed = time.perf_counter() - started
        # Give the last batches time to show up in a snapshot.
        grace_deadline = time.perf_counter() + 5
        # Polling a plain list is enough for a one-off 5 s grace period in a benchmark.
        while stats.pending and time.perf_counter() < grace_deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.1)
        stop.set()
        await ws_task

    return {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "config": {
            "api": args.api,
            "offered_rate_events_per_s": args.rate or "unlimited",
            "duration_s": args.duration,
            "batch_size": args.batch_size,
            "concurrency": args.concurrency,
        },
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpus": os.cpu_count(),
        },
        "results": {
            "measured_duration_s": round(elapsed, 2),
            "accepted_events": stats.accepted,
            "accepted_events_per_s": round(stats.accepted / elapsed, 1),
            "batches": stats.batches,
            "http_429": stats.rejected_429,
            "other_errors": stats.other_errors,
            "ack_latency_ms": percentiles(stats.ack_ms),
            "end_to_end_latency_ms": percentiles(stats.e2e_ms),
            "e2e_samples": len(stats.e2e_ms),
            "batches_never_seen_in_a_snapshot": len(stats.pending),
            "snapshots_received": stats.snapshots,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest throughput and end-to-end latency benchmark"
    )
    parser.add_argument("--api", default="http://localhost:8080")
    parser.add_argument("--ws", default="ws://localhost:8080/ws/live")
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--rate", type=float, default=1000, help="offered events/s; 0 = unlimited")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path, help="write the JSON result to this file")
    args = parser.parse_args()

    result = asyncio.run(run(args))
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
