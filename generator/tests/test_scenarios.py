import random

from event_generator.scenarios import Anomaly, ScenarioScheduler


def scheduler(**overrides: object) -> ScenarioScheduler:
    options: dict[str, object] = {
        "start": 0.0,
        "base_cancel_probability": 0.07,
        "base_malformed_ratio": 0.01,
        "bursts": False,
        "burst_mean_interval_s": 180.0,
        "anomalies": False,
        "anomaly_interval_s": 420.0,
        **overrides,
    }
    return ScenarioScheduler(random.Random(1), **options)  # type: ignore[arg-type]


def test_quiet_scheduler_returns_baseline() -> None:
    effects = scheduler().effects(1_000.0)
    assert (effects.rate_multiplier, effects.cancel_probability, effects.malformed_ratio) == (
        1.0,
        0.07,
        0.01,
    )
    assert effects.labels == ()


def test_cancellation_spike_starts_and_ends() -> None:
    s = scheduler(anomalies=True, anomaly_interval_s=10_000.0)
    s.trigger(Anomaly.CANCELLATION_SPIKE, now=100.0)
    during = s.effects(150.0)
    assert during.cancel_probability == 0.55
    assert "cancellation_spike" in during.labels
    assert s.effects(100.0 + 241).cancel_probability == 0.07


def test_revenue_drop_and_bad_producer_effects() -> None:
    s = scheduler(anomalies=True, anomaly_interval_s=10_000.0)
    s.trigger(Anomaly.REVENUE_DROP, now=0.0)
    assert s.effects(1.0).rate_multiplier == 0.1
    s.trigger(Anomaly.BAD_PRODUCER, now=500.0)
    assert s.effects(501.0).malformed_ratio == 0.25


def test_bursts_multiply_rate_for_a_while() -> None:
    s = scheduler(bursts=True, burst_mean_interval_s=5.0)
    multipliers = [s.effects(t / 2).rate_multiplier for t in range(2_000)]
    bursting = [m for m in multipliers if m > 1]
    assert bursting, "expected at least one burst in 1000 simulated seconds"
    assert all(3 <= m <= 5 for m in bursting)
    assert len(bursting) < len(multipliers)
