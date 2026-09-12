"""Sends buffered events to the API in batches, honouring backpressure."""

from __future__ import annotations

import asyncio
import logging
import random
from collections import deque
from dataclasses import dataclass
from typing import Any

import httpx
import orjson

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 502, 503, 504}


@dataclass(slots=True)
class SenderStats:
    requests: int = 0
    accepted: int = 0
    rejected: int = 0
    retries: int = 0
    dropped_buffer_full: int = 0
    dropped_unretryable: int = 0


def backoff_delay(
    attempt: int, rng: random.Random, base_s: float = 0.2, cap_s: float = 10.0
) -> float:
    """Exponential backoff with full jitter: a random delay in [0, min(cap, base * 2^attempt)].

    The randomness spreads retries out so many clients recovering at once don't hit the
    server in synchronised waves.
    """
    return rng.uniform(0, min(cap_s, base_s * 2**attempt))


class Sender:
    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        max_batch: int,
        max_buffer: int,
        flush_interval_s: float,
        rng: random.Random,
    ) -> None:
        self._client = client
        self._url = url
        self._max_batch = max_batch
        self._max_buffer = max_buffer
        self._flush_interval_s = flush_interval_s
        self._rng = rng
        self._buffer: deque[Any] = deque()
        self.stats = SenderStats()

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def enqueue(self, items: list[Any]) -> None:
        self._buffer.extend(items)
        overflow = len(self._buffer) - self._max_buffer
        for _ in range(max(0, overflow)):
            self._buffer.popleft()  # drop the oldest: fresh data matters more for a live view
        self.stats.dropped_buffer_full += max(0, overflow)

    async def run(self, stop: asyncio.Event) -> None:
        while not (stop.is_set() and not self._buffer):
            if not self._buffer:
                await asyncio.sleep(self._flush_interval_s)
                continue
            batch = [self._buffer.popleft() for _ in range(min(self._max_batch, len(self._buffer)))]
            await self.send_with_retry(batch, stop)
            if len(self._buffer) < self._max_batch:
                await asyncio.sleep(self._flush_interval_s)

    async def send_with_retry(self, batch: list[Any], stop: asyncio.Event) -> bool:
        attempt = 0
        body = orjson.dumps(batch)
        while True:
            outcome, retry_after = await self._post(body, len(batch))
            if outcome != "retry":
                return outcome == "ok"
            if stop.is_set():
                return False
            self.stats.retries += 1
            delay = retry_after if retry_after is not None else backoff_delay(attempt, self._rng)
            attempt += 1
            await asyncio.sleep(delay)

    async def _post(self, body: bytes, size: int) -> tuple[str, float | None]:
        self.stats.requests += 1
        try:
            response = await self._client.post(
                self._url, content=body, headers={"content-type": "application/json"}
            )
        except httpx.TransportError as exc:
            log.warning("API unreachable (%s); will retry", type(exc).__name__)
            return "retry", None

        if response.status_code == 202:
            result = response.json()
            self.stats.accepted += int(result["accepted"])
            self.stats.rejected += int(result["rejected"])
            return "ok", None
        if response.status_code in RETRYABLE_STATUS:
            return "retry", _retry_after(response)
        # 4xx other than 429: resending the same bytes can never succeed.
        log.error(
            "dropping batch of %d: HTTP %d %s", size, response.status_code, response.text[:200]
        )
        self.stats.dropped_unretryable += size
        return "dropped", None


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
