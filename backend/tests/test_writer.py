import asyncio
from collections.abc import Sequence
from decimal import Decimal

import asyncpg
import pytest

from analytics_api.domain.events import OrderEvent, validate_event
from analytics_api.ingestion.dead_letter import DeadLetterWriter
from analytics_api.ingestion.writer import BatchWriter, CommitInfo, IngestQueueFull, WriterStopped

from .factories import event_payload


def events(n: int) -> list[OrderEvent]:
    return [validate_event(event_payload()) for _ in range(n)]


def make_writer(db: asyncpg.Pool, **kwargs: int) -> tuple[BatchWriter, DeadLetterWriter]:
    dead_letters = DeadLetterWriter(db)
    options = {"max_queued_events": 1_000, "max_batch_events": 500, **kwargs}
    return BatchWriter(db, dead_letters, **options, retry_initial_s=0.01), dead_letters


async def test_concurrent_requests_are_group_committed(db: asyncpg.Pool) -> None:
    writer, _ = make_writer(db)
    commits: list[CommitInfo] = []
    writer.add_listener(commits.append)

    # Submitted before the writer runs, so all of them are waiting when it starts.
    futures = [writer.submit(events(10)) for _ in range(5)]
    writer.start()
    await asyncio.gather(*futures)
    await writer.stop()

    assert await db.fetchval("SELECT count(*) FROM events") == 50
    assert len(commits) == 1
    assert commits[0].inserted == 50
    assert writer.committed_seq == 50


async def test_batches_respect_max_size(db: asyncpg.Pool) -> None:
    writer, _ = make_writer(db, max_batch_events=25)
    commits: list[CommitInfo] = []
    writer.add_listener(commits.append)
    futures = [writer.submit(events(10)) for _ in range(5)]
    writer.start()
    await asyncio.gather(*futures)
    await writer.stop()
    assert [c.received for c in commits] == [20, 20, 10]


async def test_full_queue_rejects_new_work(db: asyncpg.Pool) -> None:
    writer, _ = make_writer(db, max_queued_events=5)
    writer.submit(events(3))
    with pytest.raises(IngestQueueFull):
        writer.submit(events(3))
    assert writer.queued_events == 3


async def test_stop_drains_queue_then_refuses_work(db: asyncpg.Pool) -> None:
    writer, _ = make_writer(db)
    writer.start()
    future = writer.submit(events(7))
    await writer.stop()
    assert future.done() and future.exception() is None
    assert await db.fetchval("SELECT count(*) FROM events") == 7
    with pytest.raises(WriterStopped):
        writer.submit(events(1))


async def test_row_rejected_by_database_is_isolated_and_dead_lettered(db: asyncpg.Pool) -> None:
    writer, dead_letters = make_writer(db)
    batch = events(9)
    # model_copy skips validation: simulates a row that slipped past Pydantic but violates
    # the database CHECK (amount_mad > 0).
    batch.insert(
        4,
        batch[0].model_copy(
            update={"event_id": events(1)[0].event_id, "amount_mad": Decimal("-1")}
        ),
    )
    writer.start()
    await writer.submit(batch)
    await writer.stop()
    await dead_letters.flush()

    assert await db.fetchval("SELECT count(*) FROM events") == 9
    assert await db.fetchval("SELECT reason FROM dead_letter_events") == "database_rejected"


class FlakyWriter(BatchWriter):
    """Fails the first two attempts as if the database connection dropped."""

    failures_left = 2

    async def _execute(self, events: Sequence[OrderEvent]) -> tuple[int, int | None]:
        if self.failures_left:
            self.failures_left -= 1
            raise ConnectionResetError("simulated network failure")
        return await super()._execute(events)


async def test_transient_failures_are_retried(db: asyncpg.Pool) -> None:
    writer = FlakyWriter(
        db, DeadLetterWriter(db), max_queued_events=100, max_batch_events=100, retry_initial_s=0.01
    )
    writer.start()
    await asyncio.wait_for(writer.submit(events(4)), timeout=5)
    await writer.stop()
    assert writer.failures_left == 0
    assert await db.fetchval("SELECT count(*) FROM events") == 4
