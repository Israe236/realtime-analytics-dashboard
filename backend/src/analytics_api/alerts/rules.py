"""Alert rules — pure functions, no I/O.

Each rule looks at :class:`AlertInputs` (numbers already read from the aggregate tables)
and returns an :class:`Evaluation`:

* ``value``     – the measured number, or ``None`` when there is not enough data to judge
  (e.g. 3 orders in five minutes: one cancellation would already be 33 %).
* ``breached``  – value is at/over the firing threshold.
* ``recovered`` – value is back under the (lower) resolve threshold.

Two thresholds (fire at 25 %, resolve under 20 %) are *hysteresis*: a value hovering around
25 % does not make the alert flap between firing and resolved every few seconds.
:class:`RuleTracker` adds a second guard — the condition must hold for several consecutive
evaluations before the state changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar

from analytics_api.realtime.protocol import AlertSeverity


@dataclass(frozen=True)
class AlertInputs:
    window_minutes: int
    # Current window, including the minute still in progress.
    placed: int
    cancelled: int
    accepted: int
    rejected: int
    # Complete minutes only: comparing a half-finished minute to a full one would always
    # look like a drop.
    revenue_current_mad: Decimal
    revenue_previous_mad: Decimal
    seconds_since_last_event: float | None


@dataclass(frozen=True)
class Evaluation:
    value: float | None
    breached: bool
    recovered: bool
    message: str


def _no_data(message: str) -> Evaluation:
    return Evaluation(value=None, breached=False, recovered=False, message=message)


class Rule(ABC):
    name: ClassVar[str]
    severity: ClassVar[AlertSeverity]
    threshold: float

    @abstractmethod
    def evaluate(self, inputs: AlertInputs) -> Evaluation: ...


@dataclass(frozen=True)
class CancellationRateRule(Rule):
    name: ClassVar[str] = "cancellation_rate"
    severity: ClassVar[AlertSeverity] = AlertSeverity.WARNING
    threshold: float = 0.25
    resolve_below: float = 0.20
    min_orders: int = 30

    def evaluate(self, inputs: AlertInputs) -> Evaluation:
        if inputs.placed < self.min_orders:
            return _no_data(f"fewer than {self.min_orders} orders in the window")
        rate = inputs.cancelled / inputs.placed
        return Evaluation(
            value=rate,
            breached=rate >= self.threshold,
            recovered=rate < self.resolve_below,
            message=(
                f"Cancellation rate {rate:.0%} over the last {inputs.window_minutes} min "
                f"({inputs.cancelled} of {inputs.placed} orders); threshold {self.threshold:.0%}"
            ),
        )


@dataclass(frozen=True)
class RevenueDropRule(Rule):
    name: ClassVar[str] = "revenue_drop"
    severity: ClassVar[AlertSeverity] = AlertSeverity.CRITICAL
    threshold: float = 0.5
    resolve_below: float = 0.3
    min_baseline_mad: float = 2_000.0

    def evaluate(self, inputs: AlertInputs) -> Evaluation:
        previous = inputs.revenue_previous_mad
        if previous < Decimal(str(self.min_baseline_mad)):
            # With a tiny baseline, 200 → 80 MAD is a "60 % drop" that means nothing.
            return _no_data(f"previous-period revenue below {self.min_baseline_mad:,.0f} MAD")
        drop = float(1 - inputs.revenue_current_mad / previous)
        return Evaluation(
            value=drop,
            breached=drop >= self.threshold,
            recovered=drop < self.resolve_below,
            message=(
                f"Revenue down {drop:.0%}: {inputs.revenue_current_mad:,.0f} MAD in the last "
                f"{inputs.window_minutes} min vs {previous:,.0f} MAD before; "
                f"threshold {self.threshold:.0%}"
            ),
        )


@dataclass(frozen=True)
class DeadLetterRateRule(Rule):
    name: ClassVar[str] = "dead_letter_rate"
    severity: ClassVar[AlertSeverity] = AlertSeverity.WARNING
    threshold: float = 0.05
    resolve_below: float = 0.03
    min_events: int = 100

    def evaluate(self, inputs: AlertInputs) -> Evaluation:
        received = inputs.accepted + inputs.rejected
        if received < self.min_events:
            return _no_data(f"fewer than {self.min_events} events received in the window")
        rate = inputs.rejected / received
        return Evaluation(
            value=rate,
            breached=rate >= self.threshold,
            recovered=rate < self.resolve_below,
            message=(
                f"{rate:.1%} of incoming events rejected over the last {inputs.window_minutes} min "
                f"({inputs.rejected} of {received}); threshold {self.threshold:.0%}"
            ),
        )


@dataclass(frozen=True)
class IngestionStalledRule(Rule):
    name: ClassVar[str] = "ingestion_stalled"
    severity: ClassVar[AlertSeverity] = AlertSeverity.CRITICAL
    threshold: float = 30.0  # seconds

    def evaluate(self, inputs: AlertInputs) -> Evaluation:
        silence = inputs.seconds_since_last_event
        if silence is None:
            return _no_data("no events received since start-up")
        return Evaluation(
            value=silence,
            breached=silence >= self.threshold,
            recovered=silence < self.threshold,
            message=f"No events ingested for {silence:.0f} s; threshold {self.threshold:.0f} s",
        )


class Transition(StrEnum):
    FIRE = "fire"
    RESOLVE = "resolve"


@dataclass
class RuleTracker:
    """Firing/resolved state of one rule, changed only after N consecutive evaluations."""

    fire_after: int = 2
    resolve_after: int = 2
    firing: bool = False
    streak: int = 0

    def step(self, evaluation: Evaluation) -> Transition | None:
        if evaluation.value is None:
            # Not enough data: keep the current state, but restart the count.
            self.streak = 0
            return None
        condition = evaluation.recovered if self.firing else evaluation.breached
        self.streak = self.streak + 1 if condition else 0
        needed = self.resolve_after if self.firing else self.fire_after
        if self.streak < needed:
            return None
        self.streak = 0
        self.firing = not self.firing
        return Transition.FIRE if self.firing else Transition.RESOLVE
