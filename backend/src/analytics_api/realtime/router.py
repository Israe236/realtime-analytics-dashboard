from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket
from fastapi.responses import Response
from starlette.websockets import WebSocketDisconnect

from analytics_api.realtime.hub import ClientOverflow
from analytics_api.services import ServicesDep

log = logging.getLogger(__name__)

router = APIRouter()

# WebSocket close codes (RFC 6455 / IANA registry).
TRY_AGAIN_LATER = 1013
POLICY_VIOLATION = 1008


async def _drain_incoming(websocket: WebSocket) -> None:
    """Clients send nothing we need, but reading is how a disconnect is noticed."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


@router.websocket("/ws/live")
async def live(websocket: WebSocket, services: ServicesDep) -> None:
    await websocket.accept()
    client = services.hub.register(websocket)
    if client is None:
        await websocket.close(TRY_AGAIN_LATER, "server at client capacity")
        return
    try:
        client.offer(services.broadcaster.hello())
        if services.broadcaster.latest_snapshot is not None:
            client.offer_latest("snapshot", services.broadcaster.latest_snapshot)

        sender = asyncio.create_task(client.run_sender())
        receiver = asyncio.create_task(_drain_incoming(websocket))
        done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        if sender in done:
            error = sender.exception()
            if isinstance(error, ClientOverflow):
                log.warning("disconnecting slow WebSocket client (queue overflow)")
                with contextlib.suppress(Exception):
                    await websocket.close(POLICY_VIOLATION, "client too slow")
            elif isinstance(error, TimeoutError):
                log.warning("disconnecting WebSocket client (send timeout)")
            elif error is not None and not isinstance(error, WebSocketDisconnect):
                log.info("WebSocket send failed: %r", error)
    finally:
        services.hub.unregister(client)


@router.get("/api/metrics/snapshot", tags=["metrics"])
async def latest_snapshot(services: ServicesDep) -> Response:
    """The most recent snapshot (same JSON as the WebSocket ``snapshot`` message)."""
    if services.broadcaster.latest_snapshot is None:
        await services.broadcaster.broadcast_once()
    return Response(services.broadcaster.latest_snapshot, media_type="application/json")
