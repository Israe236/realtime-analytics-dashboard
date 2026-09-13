from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class RateMeter:
    """Events per second over a sliding time window, in O(1) memory per second of window."""

    def __init__(self, window_s: float = 10.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._window_s = window_s
        self._clock = clock
        self._started = clock()
        self._samples: deque[tuple[float, int]] = deque()
        self._total = 0

    def add(self, count: int) -> None:
        now = self._clock()
        self._samples.append((now, count))
        self._total += count
        self._prune(now)

    def rate(self) -> float:
        now = self._clock()
        self._prune(now)
        # Right after start-up, divide by the time actually observed, not the full window,
        # otherwise the first seconds would under-report.
        observed = min(self._window_s, max(now - self._started, 1.0))
        return self._total / observed

    def _prune(self, now: float) -> None:
        horizon = now - self._window_s
        while self._samples and self._samples[0][0] < horizon:
            _, count = self._samples.popleft()
            self._total -= count
