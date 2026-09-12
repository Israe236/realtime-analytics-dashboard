"""Order lifecycle simulation.

Each new order emits ``order_placed`` immediately and schedules its follow-ups on a
min-heap ordered by due time:

* Prepaid (card, wallet, bank transfer): placed → paid → shipped, or placed → cancelled.
* Cash on delivery: placed → shipped → paid (the courier collects the cash), or
  placed → cancelled — and COD orders are cancelled more often.

Follow-up delays are a few seconds to a minute of *real* time so that a dashboard shows
the whole lifecycle quickly.
"""

from __future__ import annotations

import heapq
import itertools
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from event_generator.catalog import CITIES, PAYMENT_METHODS, order_amount, pick, pick_category

COD_CANCEL_FACTOR = 1.5
PREPAID_CANCEL_FACTOR = 0.7


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    customer_id: str
    category: str
    city: str
    payment_method: str
    amount_mad: str

    def event(self, event_type: str, occurred_at: datetime) -> dict[str, Any]:
        return {
            "event_id": str(uuid.uuid4()),
            "order_id": self.order_id,
            "event_type": event_type,
            "occurred_at": occurred_at.isoformat(),
            "amount_mad": self.amount_mad,
            "category": self.category,
            "city": self.city,
            "payment_method": self.payment_method,
            "customer_id": self.customer_id,
        }


@dataclass(order=True, slots=True)
class _FollowUp:
    due: float
    tiebreak: int
    event_type: str = field(compare=False)
    order: Order = field(compare=False)
    then: tuple[tuple[str, float, float], ...] = field(compare=False, default=())


def expected_events_per_order(cancel_probability: float) -> float:
    """Average number of events one order produces (placed + 2 follow-ups, or + 1 cancel)."""
    return 3.0 - min(cancel_probability, 1.0)


class OrderSimulator:
    def __init__(self, rng: random.Random, *, max_pending: int = 500_000) -> None:
        self._rng = rng
        self._max_pending = max_pending
        self._heap: list[_FollowUp] = []
        self._counter = itertools.count()
        self.skipped_follow_ups = 0

    @property
    def pending(self) -> int:
        return len(self._heap)

    def new_order(self, now: float, cancel_probability: float) -> dict[str, Any]:
        rng = self._rng
        category = pick_category(rng)
        order = Order(
            order_id=str(uuid.uuid4()),
            customer_id=f"cust-{rng.randint(1, 250_000):06d}",
            category=category.name,
            city=pick(rng, CITIES),
            payment_method=pick(rng, PAYMENT_METHODS),
            amount_mad=order_amount(rng, category),
        )
        cod = order.payment_method == "cash_on_delivery"
        factor = COD_CANCEL_FACTOR if cod else PREPAID_CANCEL_FACTOR
        if rng.random() < min(cancel_probability * factor, 0.95):
            self._schedule(now, order, "order_cancelled", 5, 60)
        elif cod:
            self._schedule(now, order, "order_shipped", 5, 40, then=(("order_paid", 10, 60),))
        else:
            self._schedule(now, order, "order_paid", 2, 20, then=(("order_shipped", 10, 60),))
        return order.event("order_placed", datetime.now(UTC))

    def due_events(self, now: float) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        wall = datetime.now(UTC)
        while self._heap and self._heap[0].due <= now:
            item = heapq.heappop(self._heap)
            events.append(item.order.event(item.event_type, wall))
            if item.then:
                (event_type, low, high), *rest = item.then
                self._schedule(now, item.order, event_type, low, high, then=tuple(rest))
        return events

    def _schedule(
        self,
        now: float,
        order: Order,
        event_type: str,
        low_s: float,
        high_s: float,
        then: tuple[tuple[str, float, float], ...] = (),
    ) -> None:
        if len(self._heap) >= self._max_pending:
            self.skipped_follow_ups += 1
            return
        due = now + self._rng.uniform(low_s, high_s)
        heapq.heappush(self._heap, _FollowUp(due, next(self._counter), event_type, order, then))
