"""WebSocket message contract (server → client).

These models are the single source of truth for the frontends: ``scripts/gen_ts_types.py``
exports their JSON Schema and generates the TypeScript types used by the React, Angular
and React Native apps. Every message has a ``type`` field that tells them apart.

Money is sent as a JSON number (float) here — exact ``Decimal`` arithmetic is done in
Postgres; the browser only needs to display the result.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

import orjson
from pydantic import BaseModel, ConfigDict, Field

from analytics_api.domain.events import Category, EventType, PaymentMethod


class _Message(BaseModel):
    # In the exported schema, fields with defaults (like ``type``) are still marked required,
    # because the server always sends them.
    model_config = ConfigDict(frozen=True, json_schema_serialization_defaults_required=True)


class Kpis(_Message):
    revenue_mad: float = Field(description="Sum of order_paid amounts")
    orders: int = Field(description="Count of order_placed")
    paid_orders: int
    shipped_orders: int
    cancelled_orders: int
    average_order_value_mad: float | None = Field(description="revenue / paid orders")
    cancellation_rate: float | None = Field(description="cancelled / placed, 0..1")


class SeriesPoint(_Message):
    bucket: datetime
    revenue_mad: float
    orders: int
    cancelled: int


class StatusCount(_Message):
    status: EventType
    count: int


class RankedItem(_Message):
    name: str
    revenue_mad: float
    orders: int


class PipelineHealth(_Message):
    events_per_second: float = Field(description="Accepted events/s over the last 10 s")
    dead_letter_rate: float | None = Field(description="rejected / received in the window")
    freshness_lag_ms: float | None = Field(
        description="Server time minus occurred_at of the newest committed event"
    )
    queued_events: int
    connected_clients: int


class SnapshotMessage(_Message):
    type: Literal["snapshot"] = "snapshot"
    generated_at: datetime
    through_seq: int = Field(description="Every event with seq <= this is included")
    window_minutes: int
    kpis: Kpis
    previous_kpis: Kpis = Field(description="Same metrics for the window before this one")
    revenue_per_minute: list[SeriesPoint]
    revenue_per_hour: list[SeriesPoint]
    orders_by_status: list[StatusCount]
    top_categories: list[RankedItem]
    top_cities: list[RankedItem]
    payment_methods: list[RankedItem]
    pipeline: PipelineHealth


class FeedEvent(_Message):
    event_id: UUID
    order_id: UUID
    event_type: EventType
    occurred_at: datetime
    amount_mad: float
    category: Category
    city: str
    payment_method: PaymentMethod


class EventsMessage(_Message):
    type: Literal["events"] = "events"
    items: list[FeedEvent]


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertState(StrEnum):
    FIRING = "firing"
    RESOLVED = "resolved"


class Alert(_Message):
    id: int
    rule: str
    severity: AlertSeverity
    state: AlertState
    message: str
    value: float | None
    threshold: float
    fired_at: datetime
    resolved_at: datetime | None


class AlertMessage(_Message):
    type: Literal["alert"] = "alert"
    alert: Alert


class HelloMessage(_Message):
    type: Literal["hello"] = "hello"
    server_time: datetime
    active_alerts: list[Alert]
    recent_events: list[FeedEvent]


class PingMessage(_Message):
    type: Literal["ping"] = "ping"
    server_time: datetime


ServerMessage = Annotated[
    HelloMessage | SnapshotMessage | EventsMessage | AlertMessage | PingMessage,
    Field(discriminator="type"),
]


def encode(message: BaseModel) -> str:
    """Serialise once; the same string is then sent to every client."""
    return orjson.dumps(message.model_dump(mode="json")).decode()
