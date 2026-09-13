from dataclasses import replace
from decimal import Decimal

from analytics_api.alerts.rules import (
    AlertInputs,
    CancellationRateRule,
    DeadLetterRateRule,
    Evaluation,
    IngestionStalledRule,
    RevenueDropRule,
    RuleTracker,
    Transition,
)

HEALTHY = AlertInputs(
    window_minutes=5,
    placed=100,
    cancelled=5,
    accepted=1_000,
    rejected=5,
    revenue_current_mad=Decimal("10000"),
    revenue_previous_mad=Decimal("10000"),
    seconds_since_last_event=0.5,
)


def with_(**changes: object) -> AlertInputs:
    return replace(HEALTHY, **changes)  # type: ignore[arg-type]


# ---- cancellation rate ------------------------------------------------------------------------


def test_cancellation_rate_fires_holds_in_band_and_recovers() -> None:
    rule = CancellationRateRule(threshold=0.25, resolve_below=0.20, min_orders=30)

    high = rule.evaluate(with_(cancelled=30))
    assert (high.value, high.breached, high.recovered) == (0.3, True, False)
    assert "30%" in high.message and "30 of 100" in high.message

    band = rule.evaluate(with_(cancelled=22))  # between resolve (20 %) and fire (25 %)
    assert (band.breached, band.recovered) == (False, False)

    assert rule.evaluate(with_(cancelled=10)).recovered
    assert rule.evaluate(with_(cancelled=25)).breached  # threshold is inclusive


def test_cancellation_rate_needs_minimum_volume() -> None:
    rule = CancellationRateRule(min_orders=30)
    assert rule.evaluate(with_(placed=10, cancelled=9)).value is None
    assert rule.evaluate(with_(placed=0, cancelled=0)).value is None


# ---- revenue drop -----------------------------------------------------------------------------


def test_revenue_drop() -> None:
    rule = RevenueDropRule(threshold=0.5, resolve_below=0.3, min_baseline_mad=2_000)

    drop = rule.evaluate(with_(revenue_current_mad=Decimal("4000")))
    assert drop.value is not None and round(drop.value, 2) == 0.6
    assert drop.breached

    outage = rule.evaluate(with_(revenue_current_mad=Decimal("0")))
    assert (outage.value, outage.breached) == (1.0, True)

    growth = rule.evaluate(with_(revenue_current_mad=Decimal("12000")))
    assert growth.value is not None and growth.value < 0
    assert growth.recovered and not growth.breached


def test_revenue_drop_ignores_tiny_baselines() -> None:
    rule = RevenueDropRule(min_baseline_mad=2_000)
    tiny = with_(revenue_previous_mad=Decimal("500"), revenue_current_mad=Decimal("0"))
    assert rule.evaluate(tiny).value is None


# ---- dead letters & stalls -----------------------------------------------------------------


def test_dead_letter_rate() -> None:
    rule = DeadLetterRateRule(threshold=0.05, resolve_below=0.03, min_events=100)
    bad = rule.evaluate(with_(accepted=900, rejected=100))
    assert (bad.value, bad.breached) == (0.1, True)
    assert rule.evaluate(with_(accepted=990, rejected=10)).recovered
    assert rule.evaluate(with_(accepted=40, rejected=10)).value is None


def test_ingestion_stalled() -> None:
    rule = IngestionStalledRule(threshold=30)
    assert rule.evaluate(with_(seconds_since_last_event=None)).value is None
    assert rule.evaluate(with_(seconds_since_last_event=45.0)).breached
    assert rule.evaluate(with_(seconds_since_last_event=2.0)).recovered


# ---- state machine ----------------------------------------------------------------------------

BREACH = Evaluation(value=1.0, breached=True, recovered=False, message="")
BAND = Evaluation(value=0.5, breached=False, recovered=False, message="")
OK = Evaluation(value=0.0, breached=False, recovered=True, message="")
NO_DATA = Evaluation(value=None, breached=False, recovered=False, message="")


def run(tracker: RuleTracker, evaluations: list[Evaluation]) -> list[Transition | None]:
    return [tracker.step(e) for e in evaluations]


def test_tracker_needs_consecutive_breaches_to_fire() -> None:
    tracker = RuleTracker(fire_after=2, resolve_after=2)
    assert run(tracker, [BREACH, BAND, BREACH, OK, BREACH]) == [None] * 5
    assert tracker.step(BREACH) is Transition.FIRE
    assert tracker.firing


def test_tracker_stays_firing_until_consecutive_recoveries() -> None:
    tracker = RuleTracker(fire_after=1, resolve_after=2, firing=True)
    assert run(tracker, [BREACH, OK, BAND, OK, NO_DATA, OK]) == [None] * 6
    assert tracker.firing
    assert tracker.step(OK) is Transition.RESOLVE
    assert not tracker.firing


def test_tracker_single_step_mode() -> None:
    tracker = RuleTracker(fire_after=1, resolve_after=1)
    assert run(tracker, [BREACH, BREACH, OK, OK]) == [
        Transition.FIRE,
        None,
        Transition.RESOLVE,
        None,
    ]
