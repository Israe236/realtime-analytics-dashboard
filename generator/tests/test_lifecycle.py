"""Contract tests: whatever the generator produces must be accepted by the real API model."""

import random
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from analytics_api.domain.events import TimeBounds, validate_event
from event_generator.lifecycle import OrderSimulator, expected_events_per_order


def simulate(
    orders: int, cancel_probability: float, seed: int = 7
) -> tuple[list[dict[str, Any]], OrderSimulator]:
    simulator = OrderSimulator(random.Random(seed))
    events = [simulator.new_order(0.0, cancel_probability) for _ in range(orders)]
    for second in range(1, 400):
        events.extend(simulator.due_events(float(second)))
    return events, simulator


def test_every_generated_event_passes_api_validation() -> None:
    events, _ = simulate(1_000, 0.07)
    bounds = TimeBounds(datetime.now(UTC), timedelta(minutes=5), timedelta(days=1))
    for event in events:
        validate_event(event, bounds)


def test_lifecycles_follow_the_business_rules() -> None:
    events, simulator = simulate(2_000, 0.07)
    by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_order[event["order_id"]].append(event)

    assert simulator.pending == 0
    for order_events in by_order.values():
        sequence = tuple(e["event_type"] for e in order_events)
        if sequence[-1] == "order_cancelled":
            assert sequence == ("order_placed", "order_cancelled")
        elif order_events[0]["payment_method"] == "cash_on_delivery":
            assert sequence == ("order_placed", "order_shipped", "order_paid")
        else:
            assert sequence == ("order_placed", "order_paid", "order_shipped")
        assert len({(e["amount_mad"], e["city"], e["category"]) for e in order_events}) == 1


@pytest.mark.parametrize(("probability", "low", "high"), [(0.07, 0.05, 0.10), (0.55, 0.50, 0.70)])
def test_cancellation_share_tracks_probability(probability: float, low: float, high: float) -> None:
    events, _ = simulate(5_000, probability)
    cancelled = sum(e["event_type"] == "order_cancelled" for e in events)
    assert low < cancelled / 5_000 < high


def test_events_per_order_estimate_matches_simulation() -> None:
    events, _ = simulate(5_000, 0.07)
    assert len(events) / 5_000 == pytest.approx(expected_events_per_order(0.07), abs=0.05)
