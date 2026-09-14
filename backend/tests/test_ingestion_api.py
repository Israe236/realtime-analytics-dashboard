from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import httpx
import orjson
import pytest
from fastapi import FastAPI

from analytics_api.config import Settings
from analytics_api.main import create_app
from analytics_api.services import Services

from .factories import event_payload


@pytest.fixture
async def api(
    database_url: str, db: asyncpg.Pool, request: pytest.FixtureRequest
) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    overrides: dict[str, Any] = getattr(request, "param", {})
    app: FastAPI = create_app(Settings(database_url=database_url, **overrides))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app.state.services


async def post_batch(client: httpx.AsyncClient, body: Any) -> httpx.Response:
    content = body if isinstance(body, bytes) else orjson.dumps(body)
    return await client.post(
        "/api/events/batch", content=content, headers={"content-type": "application/json"}
    )


async def dead_letters(services: Services) -> list[asyncpg.Record]:
    await services.dead_letters.flush()
    return await services.pool.fetch("SELECT * FROM dead_letter_events ORDER BY id")


async def test_valid_batch_is_committed_before_202(api: tuple[httpx.AsyncClient, Services]) -> None:
    client, services = api
    response = await post_batch(client, [event_payload() for _ in range(3)])
    assert response.status_code == 202
    assert response.json() == {
        "accepted": 3,
        "rejected": 0,
        "errors": [],
        "committed_through_seq": 3,
    }
    # No sleep needed: the 202 is only sent after the commit.
    assert await services.pool.fetchval("SELECT count(*) FROM events") == 3


async def test_mixed_batch_accepts_good_and_dead_letters_bad(
    api: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = api
    batch = [event_payload(), event_payload(amount_mad=-5), event_payload(), {"nonsense": True}]
    response = await post_batch(client, batch)

    assert response.status_code == 202
    body = response.json()
    assert (body["accepted"], body["rejected"]) == (2, 2)
    assert [e["index"] for e in body["errors"]] == [1, 3]

    rows = await dead_letters(services)
    assert [r["reason"] for r in rows] == ["validation_error", "validation_error"]
    assert orjson.loads(rows[0]["payload"])["amount_mad"] == -5
    assert await services.pool.fetchval("SELECT count(*) FROM events") == 2
    assert await services.pool.fetchval("SELECT sum(rejected) FROM ingest_stats_minute") == 2


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b'[{"event_id": ', "malformed_json"),
        (b"\x00\x01\xffgarbage", "malformed_json"),
        (b'{"not": "an array"}', "invalid_body"),
    ],
)
async def test_unusable_body_is_400_and_dead_lettered(
    api: tuple[httpx.AsyncClient, Services], body: bytes, reason: str
) -> None:
    client, services = api
    response = await post_batch(client, body)
    assert response.status_code == 400
    rows = await dead_letters(services)
    assert [r["reason"] for r in rows] == [reason]
    assert "\x00" not in rows[0]["payload"]


async def test_duplicate_resend_is_accepted_but_not_double_counted(
    api: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = api
    batch = [event_payload() for _ in range(2)]
    for _ in range(2):
        assert (await post_batch(client, batch)).status_code == 202
    assert await services.pool.fetchval("SELECT count(*) FROM events") == 2
    assert services.writer.total_duplicates == 2


async def test_single_event_endpoint(api: tuple[httpx.AsyncClient, Services]) -> None:
    client, services = api
    ok = await client.post("/api/events", json=event_payload())
    bad = await client.post("/api/events", json=event_payload(category="cars"))
    assert ok.status_code == 202
    assert bad.status_code == 422
    assert bad.json()["detail"][0]["loc"] == ["category"]
    assert len(await dead_letters(services)) == 1


@pytest.mark.parametrize("api", [{"max_events_per_request": 5}], indirect=True)
async def test_too_many_events_is_413(api: tuple[httpx.AsyncClient, Services]) -> None:
    client, _ = api
    response = await post_batch(client, [event_payload() for _ in range(6)])
    assert response.status_code == 413


@pytest.mark.parametrize(
    "api", [{"ingest_queue_max_events": 4, "max_events_per_request": 10}], indirect=True
)
async def test_saturated_writer_answers_429_with_retry_after(
    api: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = api
    response = await post_batch(client, [event_payload() for _ in range(5)])
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert await services.pool.fetchval("SELECT count(*) FROM events") == 0


async def test_health(api: tuple[httpx.AsyncClient, Services]) -> None:
    client, _ = api
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["database"] is True
