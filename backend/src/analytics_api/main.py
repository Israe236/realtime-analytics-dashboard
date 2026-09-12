"""Application factory. Run with: ``uvicorn --factory analytics_api.main:create_app``."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from analytics_api import health
from analytics_api.config import Settings, get_settings
from analytics_api.db.migrate import apply_migrations
from analytics_api.db.pool import create_pool
from analytics_api.ingestion import router as ingestion
from analytics_api.services import Services

log = logging.getLogger("analytics_api")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = await create_pool(settings)
        async with pool.acquire() as conn:
            await apply_migrations(conn)
        services = Services.build(settings, pool)
        await services.start()
        app.state.services = services
        log.info("analytics API started")
        try:
            yield
        finally:
            await services.stop()
            await pool.close()

    app = FastAPI(title="Realtime Analytics API", version="0.1.0", lifespan=lifespan)
    app.include_router(ingestion.router)
    app.include_router(health.router)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "internal error"}, status_code=500)

    return app
