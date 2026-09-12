"""Builders for test payloads."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any


def event_payload(**overrides: Any) -> dict[str, Any]:
    """A valid JSON-shaped order event; override any field."""
    payload: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "order_id": str(uuid.uuid4()),
        "event_type": "order_placed",
        "occurred_at": datetime.now(UTC).isoformat(),
        "amount_mad": "249.90",
        "category": "fashion",
        "city": "Casablanca",
        "payment_method": "card",
        "customer_id": "cust-0001",
    }
    payload.update(overrides)
    return payload
