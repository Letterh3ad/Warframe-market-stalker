from datetime import datetime, timezone

import pytest

from wfm.analyzers.base import Context
from wfm.analyzers.revert import ANALYZER
from wfm.features.types import FeatureSet, MarketFeatures, PriceFeatures, Provenance
from wfm.models import Direction, Horizon

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _ctx(**overrides) -> Context:
    return Context(now=NOW, thresholds={"revert": {**ANALYZER.DEFAULTS, **overrides}})


def _fs(z, excess, volume_trend: float = 1.0) -> FeatureSet:
    return FeatureSet(
        slug="x",
        rank=0,
        ts=NOW,
        price=PriceFeatures(
            robust_z=z, median_90d=50.0, last_close=38.0, volume_trend=volume_trend,
            percentile_90d=0.05, median_volume_30d=25,
        ),
        market=MarketFeatures(
            market_median_return_7d=-0.01, tag="mod", tag_median_return_7d=-0.02,
            excess_return_7d=excess, cohort_size=40,
        ),
        provenance=Provenance(available=frozenset({"price", "market"}), samples={"price_90d": 90}),
    )


def test_metadata():
    assert ANALYZER.name == "revert"
    assert ANALYZER.horizon is Horizon.DAILY
    assert ANALYZER.required_features() == {"price", "market"}


def test_a_deep_negative_z_with_a_falling_cohort_relative_price_accumulates():
    signals = ANALYZER.evaluate(_fs(z=-2.4, excess=-0.12), _ctx())
    assert len(signals) == 1
    assert signals[0].direction is Direction.BUY
    assert signals[0].magnitude == pytest.approx(2.4)
    assert signals[0].evidence["robust_z"] == -2.4
    assert signals[0].evidence["excess_return_7d"] == -0.12
    assert signals[0].evidence["median_90d"] == 50.0


def test_a_high_positive_z_distributes():
    assert ANALYZER.evaluate(_fs(z=2.0, excess=0.09), _ctx())[0].direction is Direction.SELL


def test_a_z_inside_the_threshold_produces_nothing():
    assert ANALYZER.evaluate(_fs(z=-1.2, excess=-0.10), _ctx()) == []


def test_a_market_wide_drop_kills_the_reversion_buy():
    assert ANALYZER.evaluate(_fs(z=-2.4, excess=0.0), _ctx()) == []
    assert ANALYZER.evaluate(_fs(z=-2.4, excess=0.05), _ctx()) == []


def test_collapsing_volume_kills_the_reversion_buy():
    assert ANALYZER.evaluate(_fs(z=-2.4, excess=-0.12, volume_trend=0.3), _ctx()) == []


def test_collapsing_volume_does_not_block_a_distribute():
    assert ANALYZER.evaluate(_fs(z=2.2, excess=0.09, volume_trend=0.3), _ctx()) != []


def test_magnitude_and_confidence_scale_with_z():
    mild = ANALYZER.evaluate(_fs(z=-1.6, excess=-0.05), _ctx())[0]
    deep = ANALYZER.evaluate(_fs(z=-3.5, excess=-0.20), _ctx())[0]
    assert deep.magnitude > mild.magnitude
    assert deep.confidence > mild.confidence


def test_an_absent_excess_return_produces_nothing():
    assert ANALYZER.evaluate(_fs(z=-2.4, excess=None), _ctx()) == []


def test_a_none_z_produces_nothing():
    assert ANALYZER.evaluate(_fs(z=None, excess=-0.12), _ctx()) == []


def test_missing_blocks_produce_nothing():
    assert ANALYZER.evaluate(FeatureSet(slug="x", rank=0, ts=NOW), _ctx()) == []


def test_thresholds_are_configurable():
    assert ANALYZER.evaluate(_fs(z=-1.2, excess=-0.10), _ctx(z_threshold=1.0)) != []
