"""Bursts and anomalies layered on top of normal traffic.

Anomalies exist so the alerting rules have something real to catch:

* ``cancellation_spike`` – a stock-out or delivery problem: new orders are cancelled at 55 %.
* ``revenue_drop``       – a checkout outage: order rate falls to 10 % of normal.
* ``bad_producer``       – a buggy client release: 25 % of events are malformed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum


class Anomaly(StrEnum):
    CANCELLATION_SPIKE = "cancellation_spike"
    REVENUE_DROP = "revenue_drop"
    BAD_PRODUCER = "bad_producer"


@dataclass(frozen=True, slots=True)
class Effects:
    rate_multiplier: float
    cancel_probability: float
    malformed_ratio: float
    labels: tuple[str, ...]


# Durations are long enough to dominate a 5-minute alert window.
_ANOMALY_DURATION_S: dict[Anomaly, float] = {
    Anomaly.CANCELLATION_SPIKE: 240.0,
    Anomaly.REVENUE_DROP: 420.0,
    Anomaly.BAD_PRODUCER: 150.0,
}


class ScenarioScheduler:
    def __init__(
        self,
        rng: random.Random,
        *,
        start: float,
        base_cancel_probability: float,
        base_malformed_ratio: float,
        bursts: bool,
        burst_mean_interval_s: float,
        anomalies: bool,
        anomaly_interval_s: float,
    ) -> None:
        self._rng = rng
        self._base_cancel = base_cancel_probability
        self._base_malformed = base_malformed_ratio
        self._bursts = bursts
        self._burst_mean_interval_s = burst_mean_interval_s
        self._anomalies = anomalies
        self._anomaly_interval_s = anomaly_interval_s

        self._burst_until = 0.0
        self._burst_multiplier = 1.0
        self._next_burst_at = start + self._exponential(burst_mean_interval_s)
        self._anomaly: Anomaly | None = None
        self._anomaly_until = 0.0
        self._next_anomaly_at = start + anomaly_interval_s * rng.uniform(0.5, 1.0)

    def _exponential(self, mean: float) -> float:
        # Waiting times between independent random events (Poisson process).
        return self._rng.expovariate(1 / mean)

    def effects(self, now: float) -> Effects:
        labels: list[str] = []
        rate, cancel, malformed = 1.0, self._base_cancel, self._base_malformed

        if self._bursts:
            if now >= self._next_burst_at:
                self._burst_until = now + self._rng.uniform(20, 45)
                self._burst_multiplier = self._rng.uniform(3, 5)
                self._next_burst_at = self._burst_until + self._exponential(
                    self._burst_mean_interval_s
                )
            if now < self._burst_until:
                rate *= self._burst_multiplier
                labels.append("flash_sale")

        if self._anomalies:
            if self._anomaly is None and now >= self._next_anomaly_at:
                self.trigger(self._rng.choice(list(Anomaly)), now)
            if self._anomaly is not None and now >= self._anomaly_until:
                self._anomaly = None
                self._next_anomaly_at = now + self._anomaly_interval_s * self._rng.uniform(0.8, 1.2)

        if self._anomaly is Anomaly.CANCELLATION_SPIKE:
            cancel = 0.55
        elif self._anomaly is Anomaly.REVENUE_DROP:
            rate *= 0.1
        elif self._anomaly is Anomaly.BAD_PRODUCER:
            malformed = 0.25
        if self._anomaly is not None:
            labels.append(self._anomaly.value)

        return Effects(rate, cancel, malformed, tuple(labels))

    def trigger(self, anomaly: Anomaly, now: float) -> None:
        self._anomaly = anomaly
        self._anomaly_until = now + _ANOMALY_DURATION_S[anomaly]
