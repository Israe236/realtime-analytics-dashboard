"""End-to-end: a real uvicorn server, real WebSocket clients, real Postgres."""

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import asyncpg
import httpx
import orjson
import pytest
import uvicorn
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from analytics_api.config import Settings
from analytics_api.main import create_app

from .factories import event_payload

Message = dict[str, Any]


@pytest.fixture
async def server(
    database_url: str, db: asyncpg.Pool, request: pytest.FixtureRequest
) -> AsyncIterator[str]:
    overrides: dict[str, Any] = getattr(request, "param", {})
    settings = Settings(
        database_url=database_url,
        broadcast_min_interval_ms=50,
        broadcast_idle_interval_ms=200,
        **overrides,
    )
    config = uvicorn.Config(create_app(settings), host="127.0.0.1", port=0, log_level="warning")
    uv_server = uvicorn.Server(config)
    task = asyncio.create_task(uv_server.serve())
    while not uv_server.started:  # noqa: ASYNC110 - uvicorn exposes a plain bool, not an Event
        await asyncio.sleep(0.01)
    port = uv_server.servers[0].sockets[0].getsockname()[1]
    yield f"127.0.0.1:{port}"
    uv_server.should_exit = True
    await task


async def wait_for(
    ws: ClientConnection, kind: str, predicate: Callable[[Message], bool] = lambda m: True
) -> Message:
    async with asyncio.timeout(10):
        while True:
            message: Message = orjson.loads(await ws.recv())
            if message["type"] == kind and predicate(message):
                return message


async def post_events(host: str, events: list[dict[str, Any]]) -> None:
    async with httpx.AsyncClient(base_url=f"http://{host}") as http:
        response = await http.post("/api/events/batch", json=events)
        assert response.status_code == 202, response.text


async def test_two_clients_receive_snapshots_including_new_events(server: str) -> None:
    async with connect(f"ws://{server}/ws/live") as ws1, connect(f"ws://{server}/ws/live") as ws2:
        hello = await wait_for(ws1, "hello")
        assert hello["active_alerts"] == []
        await wait_for(ws2, "hello")

        await post_events(
            server,
            [event_payload(event_type="order_placed", amount_mad="120.00") for _ in range(5)]
            + [event_payload(event_type="order_paid", amount_mad="100.00") for _ in range(3)],
        )

        def includes_batch(m: Message) -> bool:
            return bool(m["through_seq"] >= 8)

        for ws in (ws1, ws2):
            snapshot = await wait_for(ws, "snapshot", includes_batch)
            assert snapshot["kpis"]["orders"] == 5
            assert snapshot["kpis"]["revenue_mad"] == 300.0
            assert snapshot["pipeline"]["connected_clients"] == 2
            feed = await wait_for(ws, "events")
            assert 1 <= len(feed["items"]) <= 12


async def test_disconnecting_client_does_not_affect_others(server: str) -> None:
    async with connect(f"ws://{server}/ws/live") as survivor:
        await wait_for(survivor, "hello")
        leaver = await connect(f"ws://{server}/ws/live")
        await wait_for(leaver, "hello")
        await leaver.close()

        await post_events(server, [event_payload(event_type="order_placed")])
        snapshot = await wait_for(survivor, "snapshot", lambda m: m["kpis"]["orders"] == 1)
        assert snapshot["pipeline"]["connected_clients"] == 1

        # Abrupt disconnect (no close handshake): the server must notice and carry on.
        rude = await connect(f"ws://{server}/ws/live")
        await wait_for(rude, "hello")
        rude.transport.abort()
        await post_events(server, [event_payload(event_type="order_placed")])
        await wait_for(survivor, "snapshot", lambda m: m["kpis"]["orders"] == 2)


@pytest.mark.parametrize("server", [{"ws_max_clients": 1}], indirect=True)
async def test_client_over_capacity_is_told_to_retry_later(server: str) -> None:
    async with connect(f"ws://{server}/ws/live") as first:
        await wait_for(first, "hello")
        async with connect(f"ws://{server}/ws/live") as second:
            with pytest.raises(ConnectionClosed) as closed:
                await wait_for(second, "hello")
            assert closed.value.rcvd is not None
            assert closed.value.rcvd.code == 1013


@pytest.mark.parametrize(
    "server", [{"alert_eval_interval_s": 0.1, "alert_cancellation_min_orders": 10}], indirect=True
)
async def test_cancellation_spike_pushes_an_alert(server: str) -> None:
    async with connect(f"ws://{server}/ws/live") as ws:
        await wait_for(ws, "hello")
        await post_events(
            server,
            [event_payload(event_type="order_placed") for _ in range(12)]
            + [event_payload(event_type="order_cancelled") for _ in range(8)],
        )
        message = await wait_for(ws, "alert")
        assert message["alert"]["rule"] == "cancellation_rate"
        assert message["alert"]["state"] == "firing"

    # A client connecting later learns about the open alert from its hello message.
    async with connect(f"ws://{server}/ws/live") as late:
        hello = await wait_for(late, "hello")
        assert [a["rule"] for a in hello["active_alerts"]] == ["cancellation_rate"]
    async with httpx.AsyncClient(base_url=f"http://{server}") as http:
        history = (await http.get("/api/alerts")).json()
    assert history[0]["rule"] == "cancellation_rate"


async def test_rest_snapshot_matches_websocket_format(server: str) -> None:
    async with httpx.AsyncClient(base_url=f"http://{server}") as http:
        body = (await http.get("/api/metrics/snapshot")).json()
    assert body["type"] == "snapshot"
    assert len(body["revenue_per_minute"]) == 60
