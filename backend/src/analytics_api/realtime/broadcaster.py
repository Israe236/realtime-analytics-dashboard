"""Turns commits into WebSocket snapshots, at a bounded rate.

Push, but throttled:

* The writer calls :meth:`Broadcaster.on_commit` after every batch (cheap, no I/O). That only
  sets a "dirty" flag and records a few events for the live feed.
* The broadcaster loop wakes on the flag, builds one snapshot from the aggregate tables,
  publishes it, then sleeps until ``min_interval`` has passed since the build started.
  However many batches commit, clients get at most ~4 snapshots per second (250 ms), and
  the database sees at most that many snapshot queries — regardless of the number of
  connected clients, because one serialised snapshot is shared by all of them.
* With no commits, it still rebuilds every ``idle_interval`` so rolling windows move and a
  stalled pipeline is visible (freshness lag keeps growing).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

from analytics_api.domain.events import OrderEvent
from analytics_api.ingestion.writer import BatchWriter, CommitInfo
from analytics_api.processing.metrics import LivePipeline, MetricsReader
from analytics_api.processing.rate_meter import RateMeter
from analytics_api.realtime.hub import Hub
from analytics_api.realtime.protocol import (
    Alert,
    AlertMessage,
    EventsMessage,
    FeedEvent,
    HelloMessage,
    PingMessage,
    encode,
)

log = logging.getLogger(__name__)


def feed_event(event: OrderEvent) -> FeedEvent:
    return FeedEvent(
        event_id=event.event_id,
        order_id=event.order_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        amount_mad=float(event.amount_mad),
        category=event.category,
        city=event.city,
        payment_method=event.payment_method,
    )


class Broadcaster:
    def __init__(
        self,
        *,
        metrics: MetricsReader,
        hub: Hub,
        writer: BatchWriter,
        rate_meter: RateMeter,
        min_interval_s: float,
        idle_interval_s: float,
        heartbeat_interval_s: float,
        feed_events_per_tick: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._metrics = metrics
        self._hub = hub
        self._writer = writer
        self._rate_meter = rate_meter
        self._min_interval_s = min_interval_s
        self._idle_interval_s = idle_interval_s
        self._heartbeat_interval_s = heartbeat_interval_s
        self._feed_per_tick = feed_events_per_tick
        self._now = now
        self._dirty = asyncio.Event()
        self._pending_feed: deque[OrderEvent] = deque(maxlen=feed_events_per_tick)
        self._recent: deque[FeedEvent] = deque(maxlen=30)
        self._newest_occurred_at: datetime | None = None
        self._task: asyncio.Task[None] | None = None
        self.active_alerts: Callable[[], list[Alert]] = list
        self.latest_snapshot: str | None = None
        self.snapshots_built = 0
        self.last_build_ms: float | None = None

    # ---- called by other components ---------------------------------------------------------

    def on_commit(self, info: CommitInfo) -> None:
        self._rate_meter.add(info.inserted)
        if info.inserted and info.newest_occurred_at is not None:
            if (
                self._newest_occurred_at is None
                or info.newest_occurred_at > self._newest_occurred_at
            ):
                self._newest_occurred_at = info.newest_occurred_at
            # A sample of each batch is enough for a human-readable feed.
            self._pending_feed.extend(info.events[-self._feed_per_tick :])
        self._dirty.set()

    def hello(self) -> str:
        return encode(
            HelloMessage(
                server_time=self._now(),
                active_alerts=self.active_alerts(),
                recent_events=list(self._recent),
            )
        )

    def publish_alert(self, alert: Alert) -> None:
        self._hub.publish(encode(AlertMessage(alert=alert)))

    # ---- lifecycle ------------------------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="broadcaster")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        last_ping = time.monotonic()
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._dirty.wait(), self._idle_interval_s)
            self._dirty.clear()
            started = time.monotonic()
            try:
                await self.broadcast_once()
            except Exception:
                # A failed snapshot (e.g. database restarting) must not kill the loop.
                log.exception("snapshot build failed; will retry on the next tick")
            if started - last_ping >= self._heartbeat_interval_s:
                self._hub.publish(encode(PingMessage(server_time=self._now())))
                last_ping = started
            await asyncio.sleep(max(0.0, self._min_interval_s - (time.monotonic() - started)))

    async def broadcast_once(self) -> None:
        # Read the watermark BEFORE querying: everything up to it is committed, so the
        # snapshot (which starts after this line) is guaranteed to include it.
        through_seq = self._writer.committed_seq
        now = self._now()
        lag_ms = (
            (now - self._newest_occurred_at).total_seconds() * 1000
            if self._newest_occurred_at is not None
            else None
        )
        live = LivePipeline(
            events_per_second=self._rate_meter.rate(),
            freshness_lag_ms=round(lag_ms, 1) if lag_ms is not None else None,
            queued_events=self._writer.queued_events,
            connected_clients=self._hub.count,
        )
        started = time.perf_counter()
        snapshot = await self._metrics.snapshot(now, through_seq=through_seq, live=live)
        self.last_build_ms = (time.perf_counter() - started) * 1000
        self.snapshots_built += 1

        self.latest_snapshot = encode(snapshot)
        self._hub.publish_latest("snapshot", self.latest_snapshot)
        if self._pending_feed:
            items = [feed_event(e) for e in self._pending_feed]
            self._pending_feed.clear()
            self._recent.extend(items)
            self._hub.publish_latest("events", encode(EventsMessage(items=items)))
