from datetime import datetime, timedelta, timezone

import pytest

from wfm.features.seasonality import bucket_of, build, profile
from wfm.models import HourlyCandle

# 2026-08-27 is a Thursday, weekday index 3, so 12:00 is bucket 3*24 + 12 = 84.
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _h(ts: datetime, volume: int, close: float) -> HourlyCandle:
    return HourlyCandle(slug="x", rank=0, ts=ts, volume=volume, close=close, median=close)


def test_bucket_of_is_hour_of_week():
    assert bucket_of(NOW) == 84
    assert bucket_of(datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc)) == 0
    assert bucket_of(datetime(2026, 8, 30, 23, 0, tzinfo=timezone.utc)) == 167


def test_profile_groups_by_bucket_and_counts_samples():
    candles = [_h(NOW - timedelta(weeks=w), volume=10 + w, close=40) for w in range(3)]
    grouped = profile(candles)
    assert grouped[84]["n"] == 3
    assert grouped[84]["volume"] == 11


def test_build_reports_deviation_against_the_current_bucket():
    history = [_h(NOW - timedelta(weeks=w), volume=10, close=40) for w in range(1, 6)]
    current = [_h(NOW, volume=25, close=48)]
    features, samples = build(history + current, now=NOW)
    assert features.bucket == 84
    assert features.expected_volume == 10
    assert features.volume_deviation == pytest.approx(1.5)
    assert features.price_deviation == pytest.approx(0.2)
    assert samples["seasonality_bucket"] == 5


def test_confidence_scales_with_sample_count_and_caps_at_one():
    two_weeks = [_h(NOW - timedelta(weeks=w), volume=10, close=40) for w in range(1, 3)]
    thin, _ = build(two_weeks + [_h(NOW, volume=10, close=40)], now=NOW, min_samples=4)
    assert thin.confidence == pytest.approx(0.5)

    six_weeks = [_h(NOW - timedelta(weeks=w), volume=10, close=40) for w in range(1, 7)]
    thick, _ = build(six_weeks + [_h(NOW, volume=10, close=40)], now=NOW, min_samples=4)
    assert thick.confidence == 1.0


def test_no_history_yields_zero_confidence_and_no_expectation():
    features, samples = build([], now=NOW)
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


def test_a_bucket_with_a_zero_expected_volume_does_not_divide_by_zero():
    candles = [_h(NOW - timedelta(weeks=w), volume=0, close=40) for w in range(1, 5)]
    features, _ = build(candles + [_h(NOW, volume=3, close=40)], now=NOW)
    assert features.volume_deviation is None


def test_deviation_reads_the_newest_closed_hour_not_a_candle_stamped_exactly_now():
    """Hourly candles come from statistics_closed, so they land on hour boundaries
    strictly in the past and nothing is ever stamped at now. Requiring ts >= now leaves
    both deviation fields permanently dead in production.
    """
    now = datetime(2026, 8, 27, 12, 37, tzinfo=timezone.utc)
    history = [_h(now.replace(minute=0) - timedelta(weeks=w), volume=10, close=40)
               for w in range(1, 6)]
    newest_closed = _h(now.replace(minute=0), volume=25, close=48)
    features, _ = build(history + [newest_closed], now=now)
    assert features.volume_deviation == pytest.approx(1.5)
    assert features.price_deviation == pytest.approx(0.2)


def test_the_newest_candle_is_not_folded_into_the_expectation_it_is_measured_against():
    now = datetime(2026, 8, 27, 12, 37, tzinfo=timezone.utc)
    history = [_h(now.replace(minute=0) - timedelta(weeks=w), volume=10, close=40)
               for w in range(1, 5)]
    features, samples = build(history + [_h(now.replace(minute=0), volume=100, close=40)],
                              now=now)
    assert features.expected_volume == 10, "the 100 must not drag the expectation up"
    assert samples["seasonality_bucket"] == 4


def test_a_bucket_only_counts_samples_that_carried_a_price():
    """n counted every entry, so a bucket of four rows with one close reported n=4 and a
    confidence of 1.0 for what is really a single observation.
    """
    now = datetime(2026, 8, 27, 12, 37, tzinfo=timezone.utc)
    at_bucket = now.replace(minute=0)
    candles = [_h(at_bucket - timedelta(weeks=w), volume=10, close=None) for w in range(1, 4)]
    candles.append(_h(at_bucket - timedelta(weeks=4), volume=10, close=40))
    grouped = profile(candles)
    assert grouped[bucket_of(at_bucket)]["n"] == 1
