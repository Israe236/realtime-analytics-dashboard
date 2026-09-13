"""Slow-client handling, tested with a fake socket we can stall on purpose."""

import asyncio

import pytest

from analytics_api.processing.rate_meter import RateMeter
from analytics_api.realtime.hub import ClientConnection, ClientOverflow, Hub


class StallableSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.gate = asyncio.Event()
        self.gate.set()

    async def send_text(self, data: str) -> None:
        await self.gate.wait()
        self.sent.append(data)


def client(socket: StallableSocket, **kw: float) -> ClientConnection:
    options = {"send_timeout_s": 1.0, "max_pending": 3, **kw}
    return ClientConnection(socket, **options)  # type: ignore[arg-type]


async def test_slow_client_only_receives_the_latest_snapshot() -> None:
    socket = StallableSocket()
    conn = client(socket)
    sender = asyncio.create_task(conn.run_sender())

    socket.gate.clear()  # the client stops reading
    conn.offer_latest("snapshot", "s1")
    await asyncio.sleep(0.01)  # s1 is now stuck "on the wire"
    for n in range(2, 7):
        conn.offer_latest("snapshot", f"s{n}")
    socket.gate.set()
    await asyncio.sleep(0.05)
    sender.cancel()

    assert socket.sent == ["s1", "s6"]
    assert conn.superseded == 4


async def test_reliable_messages_go_first_and_in_order() -> None:
    socket = StallableSocket()
    conn = client(socket)
    conn.offer_latest("snapshot", "snap")
    conn.offer("alert-1")
    conn.offer("alert-2")
    sender = asyncio.create_task(conn.run_sender())
    await asyncio.sleep(0.05)
    sender.cancel()
    assert socket.sent == ["alert-1", "alert-2", "snap"]


async def test_reliable_queue_overflow_disconnects() -> None:
    socket = StallableSocket()
    socket.gate.clear()
    conn = client(socket, send_timeout_s=10)
    sender = asyncio.create_task(conn.run_sender())
    for n in range(10):
        conn.offer(f"alert-{n}")
    socket.gate.set()
    with pytest.raises(ClientOverflow):
        await asyncio.wait_for(sender, 1)


async def test_stuck_send_times_out() -> None:
    socket = StallableSocket()
    socket.gate.clear()
    conn = client(socket, send_timeout_s=0.05)
    conn.offer_latest("snapshot", "s1")
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(conn.run_sender(), 1)


def test_hub_capacity_and_publish() -> None:
    hub = Hub(max_clients=2, send_timeout_s=1, max_pending=5)
    a, b = StallableSocket(), StallableSocket()
    conn_a, conn_b = hub.register(a), hub.register(b)
    assert conn_a is not None and conn_b is not None
    assert hub.register(StallableSocket()) is None
    hub.unregister(conn_a)
    assert hub.count == 1
    hub.publish_latest("snapshot", "x")
    assert conn_b.superseded == 0


def test_rate_meter_sliding_window() -> None:
    now = [100.0]
    meter = RateMeter(window_s=10, clock=lambda: now[0])
    now[0] = 102.0
    meter.add(50)
    assert meter.rate() == 25.0  # only 2 s observed so far
    now[0] = 110.0
    meter.add(50)
    assert meter.rate() == 10.0  # 100 events over the 10 s window
    now[0] = 113.0
    assert meter.rate() == 5.0  # the first sample left the window
