from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from analytics_api.alerts.evaluator import alert_from_row
from analytics_api.realtime.protocol import Alert
from analytics_api.services import ServicesDep

router = APIRouter(prefix="/api", tags=["alerts"])


@router.get("/alerts")
async def alert_history(
    services: ServicesDep, limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> list[Alert]:
    """Most recent alerts first, firing and resolved."""
    rows = await services.pool.fetch("SELECT * FROM alerts ORDER BY fired_at DESC LIMIT $1", limit)
    return [alert_from_row(row) for row in rows]
