import pytest

from wfm.features.price import (
    atr,
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
    return [
        _c(f"2026-06-{start_day + i:02d}", close=c, high=c + 2, low=c - 2, volume=10)
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


def test_build_on_an_empty_series_returns_an_empty_block():
    features, samples = build([])
    assert features == type(features)()
    assert samples["price_90d"] == 0


def test_build_skips_candles_with_no_close():
    candles = _series([40, 41]) + [_c("2026-06-10")]
    features, _ = build(candles)
    assert features.last_close == 41
