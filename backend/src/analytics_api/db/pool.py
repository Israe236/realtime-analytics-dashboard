from __future__ import annotations

import asyncpg

from analytics_api.config import Settings


async def create_pool(settings: Settings) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        command_timeout=30,
        # Bucketing is done with explicit 'UTC' arguments, but a UTC session keeps ad-hoc
        # queries and timestamps in logs unambiguous too.
        server_settings={"timezone": "UTC", "application_name": "analytics-api"},
    )
