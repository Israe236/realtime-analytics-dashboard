"""HTTP ingestion endpoints.

Rules of the road:
* Bad input never produces a 500 and never stops good input: each event in a batch is
  validated on its own; invalid ones are dead-lettered and reported by index.
* We parse the body ourselves (instead of letting FastAPI do it) so that even an
  unparseable body can be stored in the dead-letter table for later inspection.
* ``202 Accepted`` is only returned once the valid events are committed. If the writer is
  saturated we answer ``429`` (retry later); if the commit is slow we answer ``503``. Both
  are safe to retry because ``event_id`` makes ingestion idempotent.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError

from analytics_api.config import Settings
from analytics_api.domain.events import OrderEvent, TimeBounds, error_details, validate_event
from analytics_api.ingestion.dead_letter import DeadLetterWriter
from analytics_api.ingestion.writer import BatchWriter, IngestQueueFull, WriterStopped
from analytics_api.services import ServicesDep

router = APIRouter(prefix="/api", tags=["ingestion"])

MAX_REPORTED_ERRORS = 50


class ItemError(BaseModel):
    index: int
    errors: list[dict[str, Any]]


class IngestResult(BaseModel):
    accepted: int
    rejected: int
    errors: list[ItemError] = []


def _time_bounds(settings: Settings) -> TimeBounds:
    return TimeBounds(
        now=datetime.now(UTC),
        max_future_skew=timedelta(seconds=settings.max_future_skew_s),
        max_age=timedelta(seconds=settings.max_event_age_s),
    )


async def _read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"request body larger than {limit} bytes")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HTTPException(413, f"request body larger than {limit} bytes")
    return bytes(body)


def _decode_json(body: bytes, dead_letters: DeadLetterWriter) -> Any:
    try:
        return orjson.loads(body)
    except orjson.JSONDecodeError as exc:
        dead_letters.add(
            "malformed_json", body, [{"loc": [], "type": "json_invalid", "msg": str(exc)}]
        )
        raise HTTPException(400, "malformed JSON body") from exc


def _submit(writer: BatchWriter, events: list[OrderEvent]) -> asyncio.Future[None]:
    try:
        return writer.submit(events)
    except IngestQueueFull as exc:
        raise HTTPException(429, "ingest queue is full", headers={"Retry-After": "1"}) from exc
    except WriterStopped as exc:
        raise HTTPException(503, "server is shutting down", headers={"Retry-After": "5"}) from exc


async def _await_commit(future: asyncio.Future[None], timeout_s: float) -> None:
    try:
        # shield(): if this request times out or the client disconnects, the events still
        # get committed — cancelling the waiter must not cancel the write.
        await asyncio.wait_for(asyncio.shield(future), timeout_s)
    except TimeoutError as exc:
        raise HTTPException(
            503, "events not committed in time; safe to retry", headers={"Retry-After": "2"}
        ) from exc
    except WriterStopped as exc:
        raise HTTPException(503, "server is shutting down", headers={"Retry-After": "5"}) from exc


@router.post("/events/batch", status_code=202, response_model=IngestResult)
async def ingest_batch(request: Request, services: ServicesDep) -> IngestResult:
    settings = services.settings
    body = await _read_body(request, settings.max_request_bytes)
    data = _decode_json(body, services.dead_letters)
    if not isinstance(data, list):
        services.dead_letters.add(
            "invalid_body",
            body,
            [{"loc": [], "type": "invalid_body", "msg": "expected a JSON array"}],
        )
        raise HTTPException(400, "expected a JSON array of events")
    if len(data) > settings.max_events_per_request:
        raise HTTPException(413, f"at most {settings.max_events_per_request} events per request")

    bounds = _time_bounds(settings)
    valid: list[OrderEvent] = []
    rejected: list[tuple[int, Any, list[dict[str, Any]]]] = []
    for index, item in enumerate(data):
        try:
            valid.append(validate_event(item, bounds))
        except ValidationError as exc:
            rejected.append((index, item, error_details(exc)))

    # Submit before dead-lettering: if we answer 429 the producer resends the whole batch,
    # and the invalid items would otherwise be dead-lettered twice.
    commit = _submit(services.writer, valid)
    for _, item, errors in rejected:
        services.dead_letters.add("validation_error", item, errors)
    await _await_commit(commit, settings.ingest_ack_timeout_s)

    return IngestResult(
        accepted=len(valid),
        rejected=len(rejected),
        errors=[ItemError(index=i, errors=e) for i, _, e in rejected[:MAX_REPORTED_ERRORS]],
    )


@router.post("/events", status_code=202, response_model=IngestResult)
async def ingest_one(request: Request, services: ServicesDep) -> IngestResult:
    settings = services.settings
    body = await _read_body(request, settings.max_request_bytes)
    data = _decode_json(body, services.dead_letters)
    try:
        event = validate_event(data, _time_bounds(settings))
    except ValidationError as exc:
        details = error_details(exc)
        services.dead_letters.add("validation_error", data, details)
        raise HTTPException(422, details) from exc
    await _await_commit(_submit(services.writer, [event]), settings.ingest_ack_timeout_s)
    return IngestResult(accepted=1, rejected=0)
