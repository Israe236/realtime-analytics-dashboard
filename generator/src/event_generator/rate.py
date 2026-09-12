"""Traffic shape: a daily curve plus random flash-sale bursts."""

from __future__ import annotations

import math

# (hour of day, width in hours, height). Night trough, late-morning bump, lunch peak,
# strong evening peak — the typical shape of consumer e-commerce traffic.
_PEAKS: tuple[tuple[float, float, float], ...] = (
    (10.5, 1.8, 0.5),
    (13.0, 1.5, 0.9),
    (21.0, 2.2, 1.4),
)
_FLOOR = 0.2


def _raw_curve(hour: float) -> float:
    value = _FLOOR
    for center, width, height in _PEAKS:
        distance = abs(hour - center)
        distance = min(distance, 24 - distance)  # the day wraps around midnight
        value += height * math.exp(-0.5 * (distance / width) ** 2)
    return value


_SAMPLES = 24 * 60
_MEAN = sum(_raw_curve(24 * i / _SAMPLES) for i in range(_SAMPLES)) / _SAMPLES


def daily_multiplier(hour: float) -> float:
    """Traffic multiplier for a time of day, normalised so its 24-hour average is 1."""
    return _raw_curve(hour % 24) / _MEAN


def simulated_hour(start_hour: float, elapsed_s: float, time_scale: float) -> float:
    return (start_hour + elapsed_s * time_scale / 3600) % 24
