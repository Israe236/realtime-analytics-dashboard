import asyncio
import random

import httpx

from event_generator.sender import Sender, backoff_delay

URL = "http://api/api/events/batch"


def make_sender(client: httpx.AsyncClient, max_buffer: int = 100) -> Sender:
    return Sender(
        client,
        URL,
        max_batch=10,
        max_buffer=max_buffer,
        flush_interval_s=0.01,
        rng=random.Random(0),
    )


def accepted(n: int) -> httpx.Response:
    return httpx.Response(202, json={"accepted": n, "rejected": 0, "errors": []})


async def test_429_is_retried_after_retry_after() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "0"}) if len(calls) == 1 else accepted(2)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = make_sender(client)
        assert await sender.send_with_retry([{"a": 1}, {"b": 2}], asyncio.Event())
    assert len(calls) == 2
    assert calls[0].content == calls[1].content  # the same batch is resent
    assert (sender.stats.retries, sender.stats.accepted) == (1, 2)


async def test_connection_errors_are_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("refused")
        return accepted(1)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = make_sender(client)
        assert await sender.send_with_retry([{"a": 1}], asyncio.Event())
    assert attempts == 3


async def test_client_errors_are_not_retried() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(413, text="too big"))
    ) as client:
        sender = make_sender(client)
        assert not await sender.send_with_retry([{"a": 1}], asyncio.Event())
    assert (sender.stats.retries, sender.stats.dropped_unretryable) == (0, 1)


async def test_full_buffer_drops_oldest_events() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: accepted(0))) as client:
        sender = make_sender(client, max_buffer=100)
        sender.enqueue(list(range(150)))
        assert sender.buffered == 100
        assert sender.stats.dropped_buffer_full == 50
        stop = asyncio.Event()
        stop.set()
        await sender.run(stop)  # drains the buffer, then exits because stop is set
        assert sender.buffered == 0


def test_backoff_is_jittered_and_capped() -> None:
    rng = random.Random(0)
    delays = [backoff_delay(attempt, rng) for attempt in range(40)]
    assert all(0 <= d <= 10 for d in delays)
    assert len(set(delays)) == len(delays)
