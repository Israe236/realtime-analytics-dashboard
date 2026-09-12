from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from analytics_api.domain.events import EventType, TimeBounds, error_details, validate_event

from .factories import event_payload

BOUNDS = TimeBounds(
    now=datetime.now(UTC), max_future_skew=timedelta(minutes=5), max_age=timedelta(days=7)
)


def test_valid_event_is_parsed_and_normalised() -> None:
    offset = timezone(timedelta(hours=1))  # Morocco local time
    local_time = datetime.now(offset).replace(microsecond=0)
    event = validate_event(
        event_payload(occurred_at=local_time.isoformat(), city="  Rabat ", amount_mad=199.9),
        BOUNDS,
    )
    assert event.event_type is EventType.ORDER_PLACED
    assert event.city == "Rabat"
    assert event.amount_mad == Decimal("199.9")
    assert event.occurred_at.tzinfo == UTC
    assert event.occurred_at == local_time


@pytest.mark.parametrize("amount", ["0.01", 1, 999_999.99, "1000000"])
def test_amount_boundaries_accepted(amount: Any) -> None:
    validate_event(event_payload(amount_mad=amount), BOUNDS)


def test_unknown_fields_are_ignored() -> None:
    event = validate_event(event_payload(coupon_code="SUMMER"), BOUNDS)
    assert not hasattr(event, "coupon_code")


REQUIRED = [
    "event_id",
    "order_id",
    "event_type",
    "occurred_at",
    "amount_mad",
    "category",
    "city",
    "payment_method",
    "customer_id",
]


@pytest.mark.parametrize("field", REQUIRED)
def test_missing_required_field_is_rejected(field: str) -> None:
    payload = event_payload()
    del payload[field]
    with pytest.raises(ValidationError) as exc:
        validate_event(payload, BOUNDS)
    assert error_details(exc.value)[0]["loc"] == [field]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount_mad", 0),
        ("amount_mad", -10),
        ("amount_mad", "1000000.01"),
        ("amount_mad", "12.345"),
        ("amount_mad", "abc"),
        ("amount_mad", "NaN"),
        ("amount_mad", True),
        ("amount_mad", None),
        ("event_id", "not-a-uuid"),
        ("event_type", "order_refunded"),
        ("category", "cars"),
        ("payment_method", "bitcoin"),
        ("city", ""),
        ("city", "   "),
        ("city", "x" * 65),
        ("customer_id", 12345),
        ("occurred_at", "2026-09-12T10:00:00"),  # naive timestamp: ambiguous timezone
        ("occurred_at", "yesterday"),
    ],
)
def test_invalid_field_value_is_rejected(field: str, value: Any) -> None:
    with pytest.raises(ValidationError) as exc:
        validate_event(event_payload(**{field: value}), BOUNDS)
    assert any(err["loc"][0] == field for err in error_details(exc.value))


@pytest.mark.parametrize("raw", [None, 42, "event", [], [event_payload()]])
def test_non_object_is_rejected(raw: Any) -> None:
    with pytest.raises(ValidationError):
        validate_event(raw, BOUNDS)


def test_timestamp_bounds() -> None:
    within_skew = BOUNDS.now + timedelta(minutes=4)
    validate_event(event_payload(occurred_at=within_skew.isoformat()), BOUNDS)

    for bad in (BOUNDS.now + timedelta(minutes=6), BOUNDS.now - timedelta(days=8)):
        with pytest.raises(ValidationError):
            validate_event(event_payload(occurred_at=bad.isoformat()), BOUNDS)


def test_bounds_are_optional() -> None:
    far_future = datetime(2100, 1, 1, tzinfo=UTC).isoformat()
    validate_event(event_payload(occurred_at=far_future))


def test_error_details_do_not_echo_input() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_event(event_payload(customer_id=["secret-value"]), BOUNDS)
    details = error_details(exc.value)
    assert "secret-value" not in str(details)
    assert set(details[0]) == {"loc", "type", "msg"}
