from datetime import datetime, timedelta, timezone

import pytest

from tests.fakes.candles import hourly_at, hourly_history, same_bucket_history
from wfm.features.seasonality import bucket_of, build, profile
from wfm.models import HourlyCandle

# 2026-08-27 is a Thursday, weekday index 3, so 12:00 is bucket 3*24 + 12 = 84.
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
# A realistic wall clock: partway through an hour, which is when a poll actually runs.
MIDHOUR = datetime(2026, 8, 27, 12, 37, tzinfo=timezone.utc)


def _h(ts: datetime, volume: int, close: float) -> HourlyCandle:
    return HourlyCandle(slug="x", rank=0, ts=ts, volume=volume, close=close, median=close)


def test_bucket_of_is_hour_of_week():
    assert bucket_of(NOW) == 84
    assert bucket_of(datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc)) == 0
    assert bucket_of(datetime(2026, 8, 30, 23, 0, tzinfo=timezone.utc)) == 167


def test_profile_groups_by_bucket_and_counts_samples():
    candles = same_bucket_history(MIDHOUR, weeks=3, age_hours=1, volume=11)
    grouped = profile(candles)
    bucket = bucket_of(hourly_at(MIDHOUR, 1, 0, 0).ts)
    assert grouped[bucket]["n"] == 3
    assert grouped[bucket]["volume"] == 11


def test_a_deviation_is_reported_for_the_hour_the_item_last_traded():
    """The newest candle is whenever the item last traded, which is normally an hour or
    two back, never stamped at now. Requiring it to sit in now's bucket kills the field.
    """
    history = same_bucket_history(MIDHOUR, weeks=5, age_hours=1, volume=10, close=40)
    observation = hourly_at(MIDHOUR, age_hours=1, volume=25, close=48)
    features, samples = build(history + [observation], now=MIDHOUR)
    assert features.expected_volume == 10
    assert features.volume_deviation == pytest.approx(1.5)
    assert features.price_deviation == pytest.approx(0.2)
    assert samples["seasonality_bucket"] == 5


def test_an_observation_from_the_current_hour_is_also_accepted():
    history = same_bucket_history(MIDHOUR, weeks=5, age_hours=0, volume=10, close=40)
    observation = hourly_at(MIDHOUR, age_hours=0, volume=25, close=48)
    features, _ = build(history + [observation], now=MIDHOUR)
    assert features.volume_deviation == pytest.approx(1.5)


def test_a_stale_observation_reports_no_deviation():
    """A feed that stopped three days ago must not describe the present."""
    history = same_bucket_history(MIDHOUR, weeks=5, age_hours=72, volume=10, close=40)
    stale = hourly_at(MIDHOUR, age_hours=72, volume=25, close=48)
    features, _ = build(history + [stale], now=MIDHOUR)
    assert features.volume_deviation is None
    assert features.price_deviation is None


def test_an_exactly_one_week_old_observation_is_still_stale():
    """Hour-of-week repeats every 168 hours, so a bucket match is not recency."""
    history = same_bucket_history(MIDHOUR, weeks=5, age_hours=168, volume=10, close=40)
    week_old = hourly_at(MIDHOUR, age_hours=168, volume=25, close=48)
    features, _ = build(history + [week_old], now=MIDHOUR)
    assert features.volume_deviation is None
    assert features.price_deviation is None


def test_the_observation_is_not_folded_into_the_expectation_it_is_measured_against():
    history = same_bucket_history(MIDHOUR, weeks=4, age_hours=1, volume=10, close=40)
    observation = hourly_at(MIDHOUR, age_hours=1, volume=100, close=40)
    features, samples = build(history + [observation], now=MIDHOUR)
    assert features.expected_volume == 10, "the 100 must not drag the expectation up"
    assert samples["seasonality_bucket"] == 4


def test_the_observed_age_is_reported_so_staleness_is_visible():
    history = same_bucket_history(MIDHOUR, weeks=5, age_hours=1, volume=10, close=40)
    features, _ = build(history + [hourly_at(MIDHOUR, 1, 25, 48)], now=MIDHOUR)
    assert features.observed_age_hours == pytest.approx(1.0, abs=0.75)

    nothing, _ = build([], now=MIDHOUR)
    assert nothing.observed_age_hours is None


def test_confidence_scales_with_sample_count_and_caps_at_one():
    thin, _ = build(
        same_bucket_history(MIDHOUR, weeks=2, age_hours=1) + [hourly_at(MIDHOUR, 1, 10, 40)],
        now=MIDHOUR, min_samples=4,
    )
    assert thin.confidence == pytest.approx(0.5)

    thick, _ = build(
        same_bucket_history(MIDHOUR, weeks=6, age_hours=1) + [hourly_at(MIDHOUR, 1, 10, 40)],
        now=MIDHOUR, min_samples=4,
    )
    assert thick.confidence == 1.0


def test_no_history_yields_zero_confidence_and_no_expectation():
    features, samples = build([], now=MIDHOUR)
    assert features.confidence == 0.0
    assert features.expected_volume is None
    assert features.volume_deviation is None
    assert samples["seasonality_bucket"] == 0


def test_best_bucket_in_the_next_48_hours_is_the_highest_expected_price():
    candles = []
    for w in range(1, 5):
        candles.append(_h(NOW - timedelta(weeks=w), volume=10, close=40))
        candles.append(_h(NOW - timedelta(weeks=w) + timedelta(hours=5), volume=10, close=70))
    features, _ = build(candles + [_h(NOW, volume=10, close=40)], now=NOW)
    assert features.best_bucket_next_48h == bucket_of(NOW + timedelta(hours=5))


def test_best_bucket_is_projected_from_now_not_from_the_observation():
    features, _ = build(hourly_history(MIDHOUR, weeks=6, newest_age_hours=1), now=MIDHOUR)
    assert features.bucket == bucket_of(MIDHOUR)


def test_a_bucket_with_a_zero_expected_volume_does_not_divide_by_zero():
    history = same_bucket_history(MIDHOUR, weeks=4, age_hours=1, volume=0, close=40)
    features, _ = build(history + [hourly_at(MIDHOUR, 1, 3, 40)], now=MIDHOUR)
    assert features.volume_deviation is None


def test_a_bucket_only_counts_samples_that_carried_a_price():
    """n counted every entry, so a bucket of four rows with one close reported n=4 and a
    confidence of 1.0 for what is really a single observation.
    """
    at_bucket = hourly_at(MIDHOUR, 1, 0, 0).ts
    candles = [_h(at_bucket - timedelta(weeks=w), volume=10, close=None) for w in range(1, 4)]
    candles.append(_h(at_bucket - timedelta(weeks=4), volume=10, close=40))
    grouped = profile(candles)
    assert grouped[bucket_of(at_bucket)]["n"] == 1


def test_gaps_in_the_hourly_series_are_normal_and_not_fatal():
    """A missing hour means the item did not trade, not that data is missing."""
    sparse = [hourly_at(MIDHOUR, age_hours=h, volume=5, close=40) for h in range(1, 400, 3)]
    features, _ = build(sparse, now=MIDHOUR)
    assert features.bucket == bucket_of(MIDHOUR)
