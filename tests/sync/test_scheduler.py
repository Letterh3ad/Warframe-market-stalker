import pytest

from wfm.config import Config
from wfm.sync.scheduler import ScoreInputs, Weights, interval_minutes, score

WEIGHTS = Weights.from_config(Config())


def _inputs(volatility=0.0, volume=0.0, spread=0.0, pin=0.0) -> ScoreInputs:
    return ScoreInputs(
        volatility=volatility, volume=volume, online_spread_pct=spread, pin_weight=pin
    )


def test_a_dead_item_scores_zero():
    assert score(_inputs(), WEIGHTS) == 0.0


def test_each_term_raises_the_score():
    base = score(_inputs(), WEIGHTS)
    assert score(_inputs(volatility=0.5), WEIGHTS) > base
    assert score(_inputs(volume=100), WEIGHTS) > base
    assert score(_inputs(spread=0.4), WEIGHTS) > base
    assert score(_inputs(pin=2.0), WEIGHTS) > base


def test_missing_inputs_count_as_zero_rather_than_raising():
    assert score(ScoreInputs(None, None, None, 0.0), WEIGHTS) == 0.0


def test_volume_is_compressed_so_one_whale_item_cannot_dominate():
    modest = score(_inputs(volume=10), WEIGHTS)
    huge = score(_inputs(volume=10_000), WEIGHTS)
    assert huge < modest * 5, "volume enters logarithmically"


def test_a_pin_is_the_strongest_single_lever():
    assert score(_inputs(pin=1.0), WEIGHTS) > score(_inputs(volatility=0.2), WEIGHTS)


def test_interval_of_a_zero_score_is_the_floor():
    assert interval_minutes(0.0, floor=30, ceiling=2) == 30


def test_interval_of_a_saturated_score_is_the_ceiling():
    assert interval_minutes(99.0, floor=30, ceiling=2, saturation=1.0) == 2


def test_interval_decreases_monotonically_with_score():
    intervals = [interval_minutes(s, floor=30, ceiling=2) for s in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert intervals == sorted(intervals, reverse=True)
    assert all(2 <= i <= 30 for i in intervals)


def test_interval_never_goes_below_the_ceiling_however_large_the_score():
    assert interval_minutes(10_000.0, floor=30, ceiling=2) == 2
