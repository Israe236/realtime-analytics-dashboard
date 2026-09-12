"""Order event contract — the single source of truth for what a valid event is."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    field_validator,
)


class EventType(StrEnum):
    ORDER_PLACED = "order_placed"
    ORDER_PAID = "order_paid"
    ORDER_SHIPPED = "order_shipped"
    ORDER_CANCELLED = "order_cancelled"


class Category(StrEnum):
    ELECTRONICS = "electronics"
    FASHION = "fashion"
    HOME = "home"
    BEAUTY = "beauty"
    GROCERY = "grocery"
    SPORTS = "sports"
    BOOKS = "books"
    TOYS = "toys"


class PaymentMethod(StrEnum):
    CARD = "card"
    CASH_ON_DELIVERY = "cash_on_delivery"
    BANK_TRANSFER = "bank_transfer"
    WALLET = "wallet"


MAX_AMOUNT_MAD = Decimal("1000000")

BoundedText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
AmountMad = Annotated[
    Decimal,
    Field(gt=0, le=MAX_AMOUNT_MAD, max_digits=12, decimal_places=2, allow_inf_nan=False),
]


@dataclass(frozen=True, slots=True)
class TimeBounds:
    """Acceptable range for ``occurred_at``, relative to the server clock."""

    now: datetime
    max_future_skew: timedelta
    max_age: timedelta


class OrderEvent(BaseModel):
    """One step in an order's lifecycle.

    Unknown extra fields are ignored rather than rejected so producers can add fields
    before the API knows about them (forward compatibility).
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    event_id: UUID
    order_id: UUID
    event_type: EventType
    occurred_at: AwareDatetime
    amount_mad: AmountMad
    category: Category
    city: BoundedText
    payment_method: PaymentMethod
    customer_id: BoundedText

    @field_validator("amount_mad", mode="before")
    @classmethod
    def _reject_booleans(cls, value: Any) -> Any:
        # In Python ``True`` is an int, so lax numeric parsing would accept it as 1.00 MAD.
        if isinstance(value, bool):
            raise ValueError("amount_mad must be a number, not a boolean")
        return value

    @field_validator("occurred_at")
    @classmethod
    def _within_time_bounds(cls, value: datetime, info: ValidationInfo) -> datetime:
        bounds = info.context.get("time_bounds") if isinstance(info.context, dict) else None
        if isinstance(bounds, TimeBounds):
            if value > bounds.now + bounds.max_future_skew:
                raise ValueError("occurred_at is too far in the future")
            if value < bounds.now - bounds.max_age:
                raise ValueError("occurred_at is too old")
        return value.astimezone(UTC)


def validate_event(raw: Any, bounds: TimeBounds | None = None) -> OrderEvent:
    """Validate one decoded JSON value. Raises :class:`pydantic.ValidationError`."""
    return OrderEvent.model_validate(raw, context={"time_bounds": bounds})


def error_details(exc: ValidationError) -> list[dict[str, Any]]:
    """JSON-safe, compact description of validation errors (no echo of the input)."""
    return [
        {"loc": [str(part) for part in err["loc"]], "type": err["type"], "msg": err["msg"]}
        for err in exc.errors(include_url=False, include_context=False, include_input=False)
    ]
