"""Deliberately broken events, to exercise validation and the dead-letter path."""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

Corruption = Callable[[dict[str, Any], random.Random], Any]


def _drop_field(event: dict[str, Any], rng: random.Random) -> Any:
    broken = dict(event)
    del broken[rng.choice(list(broken))]
    return broken


def _set(field: str, value: Any) -> Corruption:
    def corrupt(event: dict[str, Any], rng: random.Random) -> Any:
        return {**event, field: value}

    return corrupt


def _naive_timestamp(event: dict[str, Any], rng: random.Random) -> Any:
    return {**event, "occurred_at": event["occurred_at"][:19]}  # strip the "+00:00"


CORRUPTIONS: tuple[Corruption, ...] = (
    _drop_field,
    _set("amount_mad", "-120.00"),
    _set("amount_mad", "abc"),
    _set("amount_mad", None),
    _set("category", "cars"),
    _set("payment_method", "bitcoin"),
    _set("event_type", "order_refunded"),
    _set("event_id", "not-a-uuid"),
    _set("city", ""),
    _naive_timestamp,
    lambda event, rng: "oops, not an object",
)


def maybe_corrupt(event: dict[str, Any], rng: random.Random, ratio: float) -> Any:
    if rng.random() >= ratio:
        return event
    return rng.choice(CORRUPTIONS)(event, rng)
