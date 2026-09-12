import random

import pytest
from pydantic import ValidationError

from analytics_api.domain.events import validate_event
from event_generator.lifecycle import OrderSimulator
from event_generator.malformed import CORRUPTIONS, Corruption, maybe_corrupt


def valid_event() -> dict[str, object]:
    return OrderSimulator(random.Random(3)).new_order(0.0, 0.0)


@pytest.mark.parametrize("corruption", CORRUPTIONS)
def test_every_corruption_is_rejected_by_the_api_model(corruption: Corruption) -> None:
    for seed in range(5):  # _drop_field picks a random field
        with pytest.raises(ValidationError):
            validate_event(corruption(valid_event(), random.Random(seed)))


def test_ratio_controls_corruption() -> None:
    rng = random.Random(0)
    event = valid_event()
    assert all(maybe_corrupt(event, rng, 0.0) is event for _ in range(100))
    assert all(maybe_corrupt(event, rng, 1.0) != event for _ in range(100))
