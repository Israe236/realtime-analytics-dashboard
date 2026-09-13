"""Long-lived application components and their start/stop order."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Annotated

import asyncpg
from fastapi import Depends
from starlette.requests import HTTPConnection

from analytics_api.alerts.evaluator import AlertEvaluator
from analytics_api.alerts.rules import (
    CancellationRateRule,
    DeadLetterRateRule,
    IngestionStalledRule,
    RevenueDropRule,
)
from analytics_api.config import Settings
from analytics_api.ingestion.dead_letter import DeadLetterWriter
from analytics_api.ingestion.writer import BatchWriter
from analytics_api.processing.metrics import MetricsReader
from analytics_api.processing.rate_meter import RateMeter
from analytics_api.realtime.broadcaster import Broadcaster
from analytics_api.realtime.hub import Hub


@dataclass
class Services:
    settings: Settings
    pool: asyncpg.Pool
    dead_letters: DeadLetterWriter
    writer: BatchWriter
    hub: Hub
    broadcaster: Broadcaster
    alerts: AlertEvaluator

    @classmethod
    def build(cls, settings: Settings, pool: asyncpg.Pool) -> Services:
        dead_letters = DeadLetterWriter(pool)
        writer = BatchWriter(
            pool,
            dead_letters,
            max_queued_events=settings.ingest_queue_max_events,
            max_batch_events=settings.writer_batch_max_events,
        )
        hub = Hub(
            max_clients=settings.ws_max_clients,
            send_timeout_s=settings.ws_send_timeout_s,
            max_pending=settings.ws_max_pending_messages,
        )
        broadcaster = Broadcaster(
            metrics=MetricsReader(
                pool,
                window_minutes=settings.metrics_window_minutes,
                hourly_hours=settings.hourly_series_hours,
            ),
            hub=hub,
            writer=writer,
            rate_meter=RateMeter(),
            min_interval_s=settings.broadcast_min_interval_ms / 1000,
            idle_interval_s=settings.broadcast_idle_interval_ms / 1000,
            heartbeat_interval_s=settings.ws_heartbeat_interval_s,
            feed_events_per_tick=settings.feed_events_per_tick,
        )
        writer.add_listener(broadcaster.on_commit)

        def seconds_since_last_event() -> float | None:
            last = writer.last_commit_monotonic
            return None if last is None else time.monotonic() - last

        ratio = settings.alert_resolve_ratio
        alerts = AlertEvaluator(
            pool,
            rules=[
                CancellationRateRule(
                    threshold=settings.alert_cancellation_rate_threshold,
                    resolve_below=settings.alert_cancellation_rate_threshold * ratio,
                    min_orders=settings.alert_cancellation_min_orders,
                ),
                RevenueDropRule(
                    threshold=settings.alert_revenue_drop_threshold,
                    resolve_below=settings.alert_revenue_drop_threshold * ratio,
                    min_baseline_mad=settings.alert_revenue_min_baseline_mad,
                ),
                DeadLetterRateRule(
                    threshold=settings.alert_dead_letter_rate_threshold,
                    resolve_below=settings.alert_dead_letter_rate_threshold * ratio,
                    min_events=settings.alert_dead_letter_min_events,
                ),
                IngestionStalledRule(threshold=settings.alert_stall_seconds),
            ],
            window_minutes=settings.alert_window_minutes,
            interval_s=settings.alert_eval_interval_s,
            fire_after=settings.alert_fire_after_evaluations,
            resolve_after=settings.alert_resolve_after_evaluations,
            seconds_since_last_event=seconds_since_last_event,
            on_change=broadcaster.publish_alert,
        )
        broadcaster.active_alerts = alerts.active_alerts
        return cls(
            settings=settings,
            pool=pool,
            dead_letters=dead_letters,
            writer=writer,
            hub=hub,
            broadcaster=broadcaster,
            alerts=alerts,
        )

    async def start(self) -> None:
        await self.alerts.load_active()
        self.dead_letters.start()
        self.writer.start()
        self.broadcaster.start()
        self.alerts.start()

    async def stop(self) -> None:
        await self.alerts.stop()
        await self.broadcaster.stop()
        # Writer before dead letters: draining it may dead-letter rows, which must be flushed.
        await self.writer.stop()
        await self.dead_letters.stop()


def get_services(conn: HTTPConnection) -> Services:
    services: Services = conn.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]
