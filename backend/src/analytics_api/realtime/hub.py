"""WebSocket fan-out that a slow client cannot slow down.

The broadcaster never awaits a client. Publishing only drops a pre-serialised string into
each client's mailbox and wakes that client's own sender task:

* **Conflated messages** (``snapshot``, ``events``): a slot holding only the *latest*
  message of each kind. A client that cannot keep up skips intermediate snapshots — each
  snapshot is complete, so the newest one is all it needs.
* **Reliable messages** (``hello``, ``alert``, ``ping``): a bounded FIFO queue. If a client
  lets it overflow, it is hopelessly behind and gets disconnected (it will reconnect
  and receive a fresh ``hello``).
* **Send timeout**: a send that does not finish in ``send_timeout_s`` (dead TCP peer, full
  socket buffer) ends the connection instead of pinning memory forever.

Memory per client is therefore bounded: one message per conflated kind plus the queue.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Protocol


class TextSocket(Protocol):
    async def send_text(self, data: str) -> None: ...


class ClientOverflow(Exception):
    """The client's reliable queue overflowed: disconnect it."""


class ClientConnection:
    def __init__(self, socket: TextSocket, *, send_timeout_s: float, max_pending: int) -> None:
        self.socket = socket
        self._send_timeout_s = send_timeout_s
        self._max_pending = max_pending
        self._queue: deque[str] = deque()
        self._latest: dict[str, str] = {}
        self._wake = asyncio.Event()
        self._overflowed = False
        self.sent = 0
        self.superseded = 0

    def offer(self, text: str) -> None:
        """Queue a message that must not be skipped."""
        if len(self._queue) >= self._max_pending:
            self._overflowed = True
        else:
            self._queue.append(text)
        self._wake.set()

    def offer_latest(self, kind: str, text: str) -> None:
        """Replace any unsent message of the same kind."""
        if kind in self._latest:
            self.superseded += 1
        self._latest[kind] = text
        self._wake.set()

    async def run_sender(self) -> None:
        """Send until an error: raises ClientOverflow, TimeoutError or a transport error."""
        while True:
            await self._wake.wait()
            self._wake.clear()
            while True:
                if self._overflowed:
                    raise ClientOverflow
                if self._queue:
                    text = self._queue.popleft()
                elif self._latest:
                    # dicts keep insertion order: send the oldest pending kind first.
                    kind = next(iter(self._latest))
                    text = self._latest.pop(kind)
                else:
                    break
                await asyncio.wait_for(self.socket.send_text(text), self._send_timeout_s)
                self.sent += 1


class Hub:
    def __init__(self, *, max_clients: int, send_timeout_s: float, max_pending: int) -> None:
        self._max_clients = max_clients
        self._send_timeout_s = send_timeout_s
        self._max_pending = max_pending
        self._clients: set[ClientConnection] = set()
        self.total_connections = 0
        self.total_disconnects = 0

    @property
    def count(self) -> int:
        return len(self._clients)

    def register(self, socket: TextSocket) -> ClientConnection | None:
        if len(self._clients) >= self._max_clients:
            return None
        client = ClientConnection(
            socket, send_timeout_s=self._send_timeout_s, max_pending=self._max_pending
        )
        self._clients.add(client)
        self.total_connections += 1
        return client

    def unregister(self, client: ClientConnection) -> None:
        if client in self._clients:
            self._clients.remove(client)
            self.total_disconnects += 1

    def publish(self, text: str) -> None:
        for client in self._clients:
            client.offer(text)

    def publish_latest(self, kind: str, text: str) -> None:
        for client in self._clients:
            client.offer_latest(kind, text)
