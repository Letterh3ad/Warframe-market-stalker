import pytest

from wfm.models import DailyCandle
from wfm.validation.harness import _forward_index, _forward_return


def _c(d: str, close: float) -> DailyCandle:
    return DailyCandle(slug="x", rank=0, date=d, close=close, high=close + 1, low=close - 1,
                       median=close, volume=10)


def _score(candles: list[DailyCandle], as_of: str, horizon_days: int) -> float | None:
    # Mirrors how replay() calls it: the series is indexed once and reused.
    return _forward_return(*_forward_index(candles), as_of, horizon_days)


def test_scores_the_first_candle_at_or_after_the_horizon():
    candles = [_c("2026-01-01", 100.0), _c("2026-01-08", 110.0)]
    assert _score(candles, "2026-01-01", 7) == pytest.approx(0.1)


def test_tolerates_a_small_gap_past_the_horizon():
    # Nearest candle is two days past the seven-day target, inside the slack.
    candles = [_c("2026-01-01", 100.0), _c("2026-01-10", 120.0)]
    assert _score(candles, "2026-01-01", 7) == pytest.approx(0.2)


def test_is_none_when_the_next_candle_is_far_past_the_horizon():
    # Nearest candle is 12 days past the target: not a seven-day return, leave unscored.
    candles = [_c("2026-01-01", 100.0), _c("2026-01-20", 200.0)]
    assert _score(candles, "2026-01-01", 7) is None


def test_is_none_when_there_is_no_later_candle_at_all():
    assert _score([_c("2026-01-01", 100.0)], "2026-01-01", 7) is None


def test_slack_never_exceeds_the_horizon():
    # horizon 1 day: a candle 3 days past the target is not a "1-day forward return".
    candles = [_c("2026-01-01", 100.0), _c("2026-01-05", 150.0)]
    assert _score(candles, "2026-01-01", 1) is None
    # the candle exactly one day past the 1-day target is inside the (capped) slack
    on_time = [_c("2026-01-01", 100.0), _c("2026-01-03", 150.0)]
    assert _score(on_time, "2026-01-01", 1) == pytest.approx(0.5)


def test_forward_index_is_reusable_across_multiple_as_of_dates():
    """The whole point of precomputing the index is that one build serves every day in
    the replay loop, not just the first call."""
    candles = [_c("2026-01-01", 100.0), _c("2026-01-08", 110.0), _c("2026-01-15", 121.0)]
    by_date, sorted_dates = _forward_index(candles)
    assert _forward_return(by_date, sorted_dates, "2026-01-01", 7) == pytest.approx(0.1)
    assert _forward_return(by_date, sorted_dates, "2026-01-08", 7) == pytest.approx(0.1)
