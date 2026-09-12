"""Long-lived application components and their start/stop order."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import asyncpg
from fastapi import Depends
from starlette.requests import HTTPConnection

from analytics_api.config import Settings
from analytics_api.ingestion.dead_letter import DeadLetterWriter
from analytics_api.ingestion.writer import BatchWriter


@dataclass
class Services:
    settings: Settings
    pool: asyncpg.Pool
    dead_letters: DeadLetterWriter
    writer: BatchWriter

    @classmethod
    def build(cls, settings: Settings, pool: asyncpg.Pool) -> Services:
        dead_letters = DeadLetterWriter(pool)
        writer = BatchWriter(
            pool,
            dead_letters,
            max_queued_events=settings.ingest_queue_max_events,
            max_batch_events=settings.writer_batch_max_events,
        )
        return cls(settings=settings, pool=pool, dead_letters=dead_letters, writer=writer)

    async def start(self) -> None:
        self.dead_letters.start()
        self.writer.start()

    async def stop(self) -> None:
        # Writer first: draining it may dead-letter rows, which the dead-letter writer flushes.
        await self.writer.stop()
        await self.dead_letters.stop()


def get_services(conn: HTTPConnection) -> Services:
    services: Services = conn.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]
