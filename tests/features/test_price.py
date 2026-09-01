from datetime import date, timedelta

import pytest

from wfm.features.price import (
    atr,
    window,
    build,
    donchian_position,
    mad,
    median,
    percentile_rank,
    robust_z,
    volume_trend,
)
from wfm.models import DailyCandle


def _c(date: str, close=None, high=None, low=None, volume=None, donch_top=None, donch_bot=None):
    return DailyCandle(
        slug="x", rank=0, date=date, close=close, high=high, low=low,
        volume=volume, median=close, donch_top=donch_top, donch_bot=donch_bot,
    )


def _series(closes: list[float], start_day: int = 1) -> list[DailyCandle]:
    start = date(2026, 6, start_day)
    return [
        _c((start + timedelta(days=i)).isoformat(), close=c, high=c + 2, low=c - 2, volume=10)
        for i, c in enumerate(closes)
    ]


def test_median_of_odd_and_even_series():
    assert median([3, 1, 2]) == 2
    assert median([1, 2, 3, 4]) == 2.5
    assert median([]) is None


def test_mad_is_the_median_of_absolute_deviations():
    # median is 4, deviations are 3, 1, 0, 1, 3, median of those is 1
    assert mad([1, 3, 4, 5, 7]) == 1


def test_mad_of_identical_values_is_zero():
    assert mad([40, 40, 40, 40]) == 0


def test_robust_z_uses_the_0_6745_scaling():
    # median 4, MAD 1, value 7: 0.6745 * (7 - 4) / 1
    assert robust_z(7, [1, 3, 4, 5, 7]) == pytest.approx(2.0235, abs=1e-4)
    assert robust_z(4, [1, 3, 4, 5, 7]) == 0.0


def test_robust_z_is_none_when_mad_is_zero_rather_than_dividing_by_zero():
    assert robust_z(50, [40, 40, 40, 40]) is None


def test_robust_z_needs_a_series():
    assert robust_z(50, []) is None
    assert robust_z(50, [40]) is None


def test_percentile_rank_is_the_share_at_or_below():
    assert percentile_rank(40, [10, 20, 30, 40]) == 1.0
    assert percentile_rank(25, [10, 20, 30, 40]) == 0.5
    assert percentile_rank(5, [10, 20, 30, 40]) == 0.0
    assert percentile_rank(5, []) is None


def test_atr_averages_true_range():
    candles = [
        _c("2026-06-01", close=40, high=42, low=38),
        _c("2026-06-02", close=44, high=46, low=41),  # tr = max(5, 6, 1) = 6
        _c("2026-06-03", close=43, high=45, low=42),  # tr = max(3, 1, 2) = 3
    ]
    assert atr(candles, window=2) == pytest.approx(4.5)


def test_atr_needs_two_candles():
    assert atr([_c("2026-06-01", close=40, high=42, low=38)], window=14) is None


def test_atr_ignores_candles_with_missing_highs():
    candles = [_c("2026-06-01", close=40), _c("2026-06-02", close=41)]
    assert atr(candles, window=14) is None


def test_volume_trend_is_short_over_long_median_volume():
    candles = [_c(f"2026-06-{i:02d}", close=40, volume=10) for i in range(1, 24)]
    candles += [_c(f"2026-06-{i:02d}", close=40, volume=30) for i in range(24, 31)]
    assert volume_trend(candles, short=7, long=30) == pytest.approx(3.0)


def test_volume_trend_is_none_without_enough_history():
    assert volume_trend(_series([40, 41]), short=7, long=30) is None


def test_donchian_position_uses_stored_bands_when_present():
    candles = _series([40, 44, 42])
    candles[-1] = _c("2026-06-03", close=42, high=44, low=40, donch_top=50, donch_bot=30)
    assert donchian_position(candles) == pytest.approx(0.6)


def test_donchian_position_falls_back_to_the_window_range():
    candles = [
        _c("2026-06-01", close=30, high=30, low=30),
        _c("2026-06-02", close=50, high=50, low=50),
        _c("2026-06-03", close=40, high=40, low=40),
    ]
    assert donchian_position(candles) == pytest.approx(0.5)


def test_donchian_position_of_a_flat_channel_is_none():
    # high == low on every candle, so the channel has zero width and dividing by it
    # would raise. _series() cannot express this: it always spreads high/low by 2.
    flat = [_c(f"2026-06-{i:02d}", close=40, high=40, low=40) for i in range(1, 4)]
    assert donchian_position(flat) is None


def test_build_reports_windows_and_sample_counts():
    candles = _series([40 + (i % 7) for i in range(90)], start_day=1)
    features, samples = build(candles)
    assert features.median_90d is not None
    assert features.median_30d is not None
    assert features.median_7d is not None
    assert features.last_close == candles[-1].close
    assert samples["price_90d"] == 90
    assert samples["price_30d"] == 30


def test_build_on_a_thin_series_leaves_long_windows_none():
    features, samples = build(_series([40, 41, 42]))
    assert features.median_7d is None
    assert features.median_90d is None
    assert features.robust_z is None
    assert samples["price_90d"] == 3


def test_a_window_is_computed_at_the_coverage_threshold_not_only_when_perfectly_full():
    # The API publishes complete days only, so a "90 day" series is 89 candles in
    # practice and a strict 90-of-90 rule would null every long window forever.
    features, _ = build(_series([40 + (i % 7) for i in range(89)]))
    assert features.median_90d is not None
    assert features.robust_z is not None
    assert features.percentile_90d is not None


def test_a_window_below_the_coverage_threshold_is_still_refused():
    features, _ = build(_series([40 + (i % 7) for i in range(80)]))
    assert features.median_90d is None
    assert features.robust_z is None
    # the 30 day window is comfortably covered by the same series
    assert features.median_30d is not None


def test_coverage_lets_a_gappy_month_through_but_not_a_thin_one():
    assert build(_series([40 + (i % 3) for i in range(27)]))[0].median_30d is not None
    assert build(_series([40 + (i % 3) for i in range(26)]))[0].median_30d is None


def _sparse(dates: list[str], close: float = 40) -> list[DailyCandle]:
    return [_c(d, close=close, high=close + 1, low=close - 1, volume=5) for d in dates]


def test_coverage_is_measured_in_calendar_days_not_in_data_points():
    """warframe.market omits untraded days entirely, so an illiquid item can carry 27
    closes spread over three months. Counting points would label that a 30 day median.
    """
    spread_over_three_months = _sparse(
        [f"2026-06-{d:02d}" for d in range(1, 10)]
        + [f"2026-07-{d:02d}" for d in range(1, 10)]
        + [f"2026-08-{d:02d}" for d in range(1, 10)]
    )
    assert len(spread_over_three_months) == 27
    features, _ = build(spread_over_three_months)
    assert features.median_30d is None


def test_a_dense_run_of_days_still_clears_the_window():
    dense = _sparse([f"2026-08-{d:02d}" for d in range(1, 31)])
    assert build(dense)[0].median_30d is not None


def test_atr_is_none_until_its_window_is_covered():
    two_days = [
        _c("2026-08-30", close=40, high=48, low=32),
        _c("2026-08-31", close=41, high=49, low=33),
    ]
    features, _ = build(two_days)
    assert features.median_30d is None
    assert features.atr_14d is None, "a 14 day ATR must not be reported from 2 candles"
    assert features.atr_pct is None


def test_donchian_position_is_none_until_its_window_is_covered():
    two_days = [
        _c("2026-08-30", close=40, high=48, low=32),
        _c("2026-08-31", close=41, high=49, low=33),
    ]
    assert build(two_days)[0].donchian_position is None


def test_a_flat_item_reports_zero_volatility_not_unmeasurable_volatility():
    flat = [_c(f"2026-08-{d:02d}", close=40, high=40, low=40, volume=5) for d in range(1, 31)]
    features, _ = build(flat)
    assert features.atr_14d == 0.0
    assert features.atr_pct == 0.0, "0.0 is a real volatility reading, None means unknown"


def test_a_window_does_not_depend_on_the_caller_having_sorted_the_candles():
    """The anchor was the last list element, not the newest date. The repo happens to
    ORDER BY date today, so this was an unguarded contract rather than a live bug.
    """
    dense = _series([40] * 30)
    assert window(list(reversed(dense)), 30) is not None
    assert len(window(list(reversed(dense)), 30)) == 30


def test_an_out_of_order_candle_does_not_drag_the_anchor_backwards():
    dense = _series([40] * 30)
    out_of_order = dense + [dense[0]]
    # the anchor is the newest date, not whatever happens to sit last in the list
    assert window(out_of_order, 30)[-1].date == dense[-1].date


def test_the_sample_count_matches_the_values_the_gate_actually_counts():
    candles = _series([40, 41]) + [_c("2026-06-05"), _c("2026-06-06")]
    _, samples = build(candles)
    assert samples["price_90d"] == 2, "provenance must not advertise closes that do not exist"


def test_build_on_an_empty_series_returns_an_empty_block():
    features, samples = build([])
    assert features == type(features)()
    assert samples["price_90d"] == 0


def test_build_skips_candles_with_no_close():
    candles = _series([40, 41]) + [_c("2026-06-10")]
    features, _ = build(candles)
    assert features.last_close == 41
