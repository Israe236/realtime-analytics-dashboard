"""Group-commit batch writer.

Requests do not talk to the database. They hand their validated events to this writer
and wait on a future. One background task drains the queue and writes everything that is
waiting in a single statement (see ``processing/aggregates.py``), then completes all the
futures at once. This is "group commit":

* Under low load a batch is whatever arrived meanwhile — often one request — so latency
  stays low (no artificial linger).
* Under high load, requests pile up while a write is in flight, so the next batch is
  bigger and the cost per event drops. Batching adapts to load by itself.
* There is exactly one writer, so aggregate upserts never contend for row locks and
  ``events.seq`` grows in commit order (which the WebSocket layer relies on).

Backpressure: the queue is bounded by *events*. When it is full, :meth:`submit` raises
:class:`IngestQueueFull` and the API answers 429 instead of buffering without limit.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from analytics_api.domain.events import OrderEvent
from analytics_api.ingestion.dead_letter import DeadLetterWriter
from analytics_api.processing.aggregates import INSERT_BATCH_SQL, batch_parameters

log = logging.getLogger(__name__)

# Errors caused by the data itself: retrying the same rows would fail forever.
_PERMANENT_ERRORS: tuple[type[BaseException], ...] = (
    asyncpg.DataError,
    asyncpg.IntegrityConstraintViolationError,
)


class IngestQueueFull(Exception):
    """The writer is saturated; the producer should retry later."""


class WriterStopped(Exception):
    """The writer is shutting down and accepts no more work."""


@dataclass(frozen=True, slots=True)
class CommitInfo:
    """What a successful batch commit produced (passed to commit listeners)."""

    inserted: int
    received: int
    max_seq: int | None
    newest_occurred_at: datetime | None
    events: Sequence[OrderEvent]


@dataclass(slots=True)
class _Pending:
    events: Sequence[OrderEvent]
    future: asyncio.Future[None]


CommitListener = Callable[[CommitInfo], None]


class BatchWriter:
    def __init__(
        self,
        pool: asyncpg.Pool,
        dead_letters: DeadLetterWriter,
        *,
        max_queued_events: int,
        max_batch_events: int,
        retry_initial_s: float = 0.1,
        retry_max_s: float = 5.0,
    ) -> None:
        self._pool = pool
        self._dead_letters = dead_letters
        self._max_queued_events = max_queued_events
        self._max_batch_events = max_batch_events
        self._retry_initial_s = retry_initial_s
        self._retry_max_s = retry_max_s
        self._pending: deque[_Pending] = deque()
        self._queued_events = 0
        self._has_work = asyncio.Event()
        self._stopping = False
        self._task: asyncio.Task[None] | None = None
        self._listeners: list[CommitListener] = []

        # Observability counters (read by health/metrics endpoints and the broadcaster).
        self.committed_seq = 0
        self.total_inserted = 0
        self.total_duplicates = 0
        self.total_batches = 0
        self.last_commit_monotonic: float | None = None

    # ---- public API -----------------------------------------------------------------------

    @property
    def queued_events(self) -> int:
        return self._queued_events

    def add_listener(self, listener: CommitListener) -> None:
        self._listeners.append(listener)

    def submit(self, events: Sequence[OrderEvent]) -> asyncio.Future[None]:
        """Queue events; the returned future completes once they are committed."""
        if self._stopping:
            raise WriterStopped
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        if not events:
            future.set_result(None)
            return future
        if self._queued_events + len(events) > self._max_queued_events:
            raise IngestQueueFull
        self._pending.append(_Pending(events, future))
        self._queued_events += len(events)
        self._has_work.set()
        return future

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="batch-writer")

    async def stop(self, timeout_s: float = 10.0) -> None:
        """Stop accepting work, drain what is queued, then exit."""
        self._stopping = True
        self._has_work.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._task, timeout_s)
        except TimeoutError:
            log.error(
                "writer did not drain within %.1fs; %d events lost", timeout_s, self._queued_events
            )
        finally:
            for pending in self._pending:
                if not pending.future.done():
                    pending.future.set_exception(WriterStopped())

    # ---- internals ------------------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            if not self._pending:
                if self._stopping:
                    return
                self._has_work.clear()
                await self._has_work.wait()
                continue
            items = self._take_batch()
            events = [event for item in items for event in item.events]
            inserted, max_seq = await self._write_until_success(events)
            self._queued_events -= len(events)
            # Update the committed watermark before waking the requests, so each 202 can
            # report a through_seq that already includes its own events.
            self._record_commit(events, inserted, max_seq)
            for item in items:
                if not item.future.done():
                    item.future.set_result(None)

    def _take_batch(self) -> list[_Pending]:
        """Pop whole requests until the batch is full (always at least one request)."""
        items = [self._pending.popleft()]
        size = len(items[0].events)
        while self._pending and size + len(self._pending[0].events) <= self._max_batch_events:
            item = self._pending.popleft()
            items.append(item)
            size += len(item.events)
        return items

    async def _write_until_success(self, events: list[OrderEvent]) -> tuple[int, int | None]:
        """Retry transient failures (DB restart, network) with capped exponential backoff."""
        delay = self._retry_initial_s
        while True:
            try:
                return await self._write_isolating_bad_rows(events)
            except Exception:
                log.exception("batch write failed; retrying in %.2fs", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._retry_max_s)

    async def _write_isolating_bad_rows(self, events: list[OrderEvent]) -> tuple[int, int | None]:
        """Write a batch; if the *data* is rejected, bisect to find and dead-letter the bad rows.

        Validation should make this unreachable, but if a row ever slips through that the
        database refuses (a constraint we forgot to mirror in Pydantic), one bad event must not
        block every good event queued behind it. Bisection costs O(log n) extra statements.
        """
        try:
            return await self._execute(events)
        except _PERMANENT_ERRORS as exc:
            if len(events) == 1:
                log.warning("dead-lettering event rejected by the database: %s", exc)
                self._dead_letters.add(
                    "database_rejected",
                    events[0].model_dump(mode="json"),
                    [{"loc": [], "type": type(exc).__name__, "msg": str(exc)}],
                )
                return 0, None
            middle = len(events) // 2
            left = await self._write_isolating_bad_rows(events[:middle])
            right = await self._write_isolating_bad_rows(events[middle:])
            max_seqs = [s for s in (left[1], right[1]) if s is not None]
            return left[0] + right[0], max(max_seqs) if max_seqs else None

    async def _execute(self, events: Sequence[OrderEvent]) -> tuple[int, int | None]:
        row = await self._pool.fetchrow(INSERT_BATCH_SQL, *batch_parameters(events))
        assert row is not None  # an aggregate SELECT always returns one row
        return int(row["inserted"]), row["max_seq"]

    def _record_commit(self, events: list[OrderEvent], inserted: int, max_seq: int | None) -> None:
        if max_seq is not None:
            self.committed_seq = max(self.committed_seq, max_seq)
        self.total_inserted += inserted
        self.total_duplicates += len(events) - inserted
        self.total_batches += 1
        self.last_commit_monotonic = time.monotonic()
        info = CommitInfo(
            inserted=inserted,
            received=len(events),
            max_seq=max_seq,
            newest_occurred_at=max(e.occurred_at for e in events),
            events=events,
        )
        for listener in self._listeners:
            try:
                listener(info)
            except Exception:
                log.exception("commit listener failed")
