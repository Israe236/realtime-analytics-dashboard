"""Dead-letter storage for input we could not accept.

Dead letters are diagnostics, not business data, so they are written asynchronously in
small batches and the request never waits for them. The in-memory buffer is bounded: if
something floods the API with garbage, we keep the first ``max_buffer`` items per flush
window and *count* the rest (``dropped``) instead of letting memory grow without limit.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

import asyncpg
import orjson

log = logging.getLogger(__name__)

_INSERT_SQL = """
WITH rows AS (
    INSERT INTO dead_letter_events (reason, errors, payload)
    SELECT reason, errors::jsonb, payload
    FROM unnest($1::text[], $2::text[], $3::text[]) AS t(reason, errors, payload)
    RETURNING 1
)
INSERT INTO ingest_stats_minute AS s (bucket, rejected)
SELECT date_trunc('minute', now(), 'UTC'), count(*) FROM rows
ON CONFLICT (bucket) DO UPDATE SET rejected = s.rejected + EXCLUDED.rejected
"""


@dataclass(frozen=True, slots=True)
class DeadLetter:
    reason: str
    errors: list[dict[str, Any]]
    payload: str


def to_payload_text(raw: bytes | str | Any, max_chars: int) -> str:
    """Printable, bounded text for any raw input. Postgres text cannot contain NUL bytes."""
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    elif isinstance(raw, str):
        text = raw
    else:
        text = orjson.dumps(raw, default=str).decode()
    text = text.replace("\x00", "\\u0000")
    if len(text) > max_chars:
        text = text[:max_chars] + f"…[truncated {len(text) - max_chars} chars]"
    return text


class DeadLetterWriter:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        max_buffer: int = 10_000,
        flush_interval_s: float = 0.5,
        flush_threshold: int = 500,
        max_payload_chars: int = 16_000,
    ) -> None:
        self._pool = pool
        self._max_buffer = max_buffer
        self._flush_interval_s = flush_interval_s
        self._flush_threshold = flush_threshold
        self._max_payload_chars = max_payload_chars
        self._buffer: list[DeadLetter] = []
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self.written = 0
        self.dropped = 0

    def add(self, reason: str, raw: bytes | str | Any, errors: list[dict[str, Any]]) -> None:
        """Record rejected input. Never raises and never blocks."""
        if len(self._buffer) >= self._max_buffer:
            self.dropped += 1
            return
        payload = to_payload_text(raw, self._max_payload_chars)
        self._buffer.append(DeadLetter(reason=reason, errors=errors, payload=payload))
        if len(self._buffer) >= self._flush_threshold:
            self._wake.set()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="dead-letter-writer")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            await self._task

    async def _run(self) -> None:
        while not self._stopping:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._flush_interval_s)
            self._wake.clear()
            await self.flush()
        await self.flush()

    async def flush(self) -> None:
        if not self._buffer:
            return
        items, self._buffer = self._buffer, []
        try:
            await self._pool.execute(
                _INSERT_SQL,
                [i.reason for i in items],
                [orjson.dumps(i.errors).decode() for i in items],
                [i.payload for i in items],
            )
            self.written += len(items)
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError):
            log.exception("failed to write %d dead letters; will retry", len(items))
            room = self._max_buffer - len(self._buffer)
            self.dropped += max(0, len(items) - room)
            self._buffer = items[:room] + self._buffer
