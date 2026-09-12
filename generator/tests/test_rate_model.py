import pytest

from event_generator.rate import daily_multiplier, simulated_hour


def test_daily_curve_averages_to_one() -> None:
    samples = [daily_multiplier(24 * i / 1440) for i in range(1440)]
    assert sum(samples) / len(samples) == pytest.approx(1.0)


def test_evening_peak_beats_lunch_beats_night() -> None:
    assert daily_multiplier(21) > daily_multiplier(13) > 1 > 0.5 > daily_multiplier(4)


def test_curve_is_continuous_across_midnight() -> None:
    assert daily_multiplier(23.999) == pytest.approx(daily_multiplier(0.0), abs=0.01)


def test_simulated_clock_wraps() -> None:
    # 600 real seconds at x12 = 2 simulated hours.
    assert simulated_hour(23.0, 600, 12) == pytest.approx(1.0)
