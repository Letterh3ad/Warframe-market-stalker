from datetime import datetime, timezone

import pytest

from wfm.analyzers.base import Context, Holding
from wfm.analyzers.selltime import ANALYZER
from wfm.features.types import (
    BookFeatures,
    FeatureSet,
    PriceFeatures,
    Provenance,
    SeasonalityFeatures,
)
from wfm.models import Direction, Horizon

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _ctx(quantity: int = 4, avg_cost: float = 30.0, **overrides) -> Context:
    holdings = (
        {("x", 0): Holding("x", 0, quantity=quantity, avg_cost=avg_cost)} if quantity else {}
    )
    return Context(
        now=NOW, holdings=holdings, thresholds={"selltime": {**ANALYZER.DEFAULTS, **overrides}}
    )


def _fs(percentile=0.92, online_bid_depth=(6, 12), confidence=0.0, best_bucket=None) -> FeatureSet:
    available = {"price", "book"}
    if confidence:
        available.add("seasonality")
    return FeatureSet(
        slug="x",
        rank=0,
        ts=NOW,
        price=PriceFeatures(percentile_90d=percentile, median_90d=45.0, last_close=58.0),
        book=BookFeatures(online_best_bid=55, online_bid_depth=online_bid_depth, online_bid_count=3),
        seasonality=SeasonalityFeatures(
            bucket=84, expected_price=50.0, confidence=confidence, best_bucket_next_48h=best_bucket
        ),
        provenance=Provenance(available=frozenset(available), samples={"price_90d": 90}),
    )


def test_metadata():
    assert ANALYZER.name == "selltime"
    assert ANALYZER.horizon is Horizon.DAILY
    assert ANALYZER.required_features() == {"price"}


def test_an_unheld_item_produces_nothing():
    assert ANALYZER.evaluate(_fs(), _ctx(quantity=0)) == []


def test_a_held_item_with_nonpositive_quantity_produces_nothing():
    assert ANALYZER.evaluate(_fs(), _ctx(quantity=-1)) == []


def test_a_none_percentile_produces_nothing():
    assert ANALYZER.evaluate(_fs(percentile=None), _ctx()) == []


def test_a_high_percentile_with_fillable_online_depth_says_sell():
    signal = ANALYZER.evaluate(_fs(), _ctx())[0]
    assert signal.direction is Direction.SELL
    assert signal.evidence["recommendation"] == "list now"
    assert signal.evidence["percentile_90d"] == 0.92
    assert signal.evidence["quantity_held"] == 4
    assert signal.evidence["avg_cost"] == 30.0
    assert signal.evidence["reference_price"] == 55
    assert signal.evidence["unrealized_pnl"] == pytest.approx((55 - 30) * 4)


def test_thin_online_bid_depth_downgrades_to_hold():
    signal = ANALYZER.evaluate(_fs(online_bid_depth=(1, 2)), _ctx(quantity=10))[0]
    assert signal.direction is Direction.HOLD
    assert "depth" in signal.evidence["reason"]


def test_a_low_percentile_says_hold():
    signal = ANALYZER.evaluate(_fs(percentile=0.2), _ctx())[0]
    assert signal.direction is Direction.HOLD
    assert signal.evidence["recommendation"] == "hold"


def test_a_confident_better_upcoming_window_is_named():
    signal = ANALYZER.evaluate(_fs(percentile=0.5, confidence=1.0, best_bucket=90), _ctx())[0]
    assert signal.direction is Direction.HOLD
    assert signal.evidence["recommendation"] == "wait"
    assert signal.evidence["wait_for_bucket"] == 90
    assert signal.evidence["seasonality_used"] is True
    assert signal.evidence["seasonality_confidence"] == 1.0


def test_thin_seasonality_is_declared_and_ignored():
    signal = ANALYZER.evaluate(_fs(percentile=0.5, confidence=0.25, best_bucket=90), _ctx())[0]
    assert signal.evidence["wait_for_bucket"] is None
    assert signal.evidence["seasonality_used"] is False
    assert signal.evidence["seasonality_confidence"] == 0.25


def test_seasonality_never_overrides_a_strong_sell():
    signal = ANALYZER.evaluate(_fs(percentile=0.95, confidence=1.0, best_bucket=90), _ctx())[0]
    assert signal.direction is Direction.SELL


def test_pnl_falls_back_to_last_close_when_there_is_no_book():
    fs = FeatureSet(
        slug="x", rank=0, ts=NOW,
        price=PriceFeatures(percentile_90d=0.9, median_90d=45.0, last_close=58.0),
        provenance=Provenance(available=frozenset({"price"}), samples={"price_90d": 90}),
    )
    signal = ANALYZER.evaluate(fs, _ctx())[0]
    assert signal.evidence["reference_price"] == 58.0
    assert signal.evidence["unrealized_pnl"] == pytest.approx((58 - 30) * 4)


def test_no_price_block_produces_nothing():
    assert ANALYZER.evaluate(FeatureSet(slug="x", rank=0, ts=NOW), _ctx()) == []
