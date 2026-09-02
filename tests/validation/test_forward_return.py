import pytest

from wfm.models import DailyCandle
from wfm.validation.harness import _forward_return


def _c(d: str, close: float) -> DailyCandle:
    return DailyCandle(slug="x", rank=0, date=d, close=close, high=close + 1, low=close - 1,
                       median=close, volume=10)


def test_scores_the_first_candle_at_or_after_the_horizon():
    candles = [_c("2026-01-01", 100.0), _c("2026-01-08", 110.0)]
    assert _forward_return(candles, "2026-01-01", 7) == pytest.approx(0.1)


def test_tolerates_a_small_gap_past_the_horizon():
    # Nearest candle is two days past the seven-day target, inside the slack.
    candles = [_c("2026-01-01", 100.0), _c("2026-01-10", 120.0)]
    assert _forward_return(candles, "2026-01-01", 7) == pytest.approx(0.2)


def test_is_none_when_the_next_candle_is_far_past_the_horizon():
    # Nearest candle is 12 days past the target: not a seven-day return, leave unscored.
    candles = [_c("2026-01-01", 100.0), _c("2026-01-20", 200.0)]
    assert _forward_return(candles, "2026-01-01", 7) is None


def test_is_none_when_there_is_no_later_candle_at_all():
    assert _forward_return([_c("2026-01-01", 100.0)], "2026-01-01", 7) is None
