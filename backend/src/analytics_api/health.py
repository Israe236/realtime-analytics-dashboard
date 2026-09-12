from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from analytics_api.services import ServicesDep

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(services: ServicesDep) -> JSONResponse:
    try:
        await asyncio.wait_for(services.pool.fetchval("SELECT 1"), timeout=2)
        database_ok = True
    except Exception:
        database_ok = False
    writer = services.writer
    body: dict[str, Any] = {
        "status": "ok" if database_ok else "degraded",
        "database": database_ok,
        "queued_events": writer.queued_events,
        "committed_seq": writer.committed_seq,
        "total_inserted": writer.total_inserted,
        "total_duplicates": writer.total_duplicates,
        "total_batches": writer.total_batches,
        "dead_letters_written": services.dead_letters.written,
        "dead_letters_dropped": services.dead_letters.dropped,
    }
    return JSONResponse(body, status_code=200 if database_ok else 503)
