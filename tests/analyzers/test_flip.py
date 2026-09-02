from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from wfm.analyzers.base import Context
from wfm.analyzers.flip import ANALYZER
from wfm.features.types import BookFeatures, FeatureSet, PriceFeatures, Provenance
from wfm.models import Direction, Horizon

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
THRESHOLDS = dict(ANALYZER.DEFAULTS)


def _ctx(**overrides) -> Context:
    return Context(now=NOW, thresholds={"flip": {**THRESHOLDS, **overrides}})


def _book(**overrides) -> BookFeatures:
    base = dict(
        online_best_ask=30,
        online_best_bid=44,
        best_ask=30,
        best_bid=44,
        ask_depth=(2, 5),
        bid_depth=(3, 7),
        online_ask_depth=(2, 5),
        online_bid_depth=(3, 7),
        online_ask_count=3,
        online_bid_count=4,
        stale_share=0.2,
        spread=-14,
        online_spread=-14,
    )
    base.update(overrides)
    return BookFeatures(**base)


def _fs(price: PriceFeatures | None = None, **book_overrides) -> FeatureSet:
    return FeatureSet(
        slug="x",
        rank=0,
        ts=NOW,
        price=price or PriceFeatures(median_7d=50.0, median_30d=52.0, median_volume_30d=40),
        book=_book(**book_overrides),
        provenance=Provenance(available=frozenset({"price", "book"}), samples={"book": 60}),
    )


def test_metadata():
    assert ANALYZER.name == "flip"
    assert ANALYZER.horizon is Horizon.URGENT
    assert ANALYZER.required_features() == {"price", "book"}


def test_a_cheap_online_ask_fires_a_buy_with_self_contained_evidence():
    signals = ANALYZER.evaluate(_fs(), _ctx())
    assert len(signals) == 1
    signal = signals[0]
    assert signal.direction is Direction.BUY
    assert signal.magnitude == pytest.approx(20.0)
    assert signal.horizon is Horizon.URGENT
    assert signal.expires_at == NOW + timedelta(minutes=THRESHOLDS["expiry_minutes"])
    assert signal.evidence["fair_value"] == 50.0
    assert signal.evidence["fair_value_source"] == "median_7d"
    assert signal.evidence["online_best_ask"] == 30
    assert signal.evidence["margin_plat"] == pytest.approx(20.0)
    assert signal.evidence["margin_pct"] == pytest.approx(0.4)
    assert signal.evidence["median_volume_30d"] == 40
    assert signal.evidence["quantity_at_best_ask"] == 2


def test_a_crossed_book_is_flagged_in_the_evidence_but_is_not_the_trigger():
    assert ANALYZER.evaluate(_fs(), _ctx())[0].evidence["crossed_book"] is True
    assert ANALYZER.evaluate(_fs(online_best_bid=20), _ctx())[0].evidence["crossed_book"] is False


def test_fair_value_falls_back_to_the_thirty_day_median():
    fs = _fs(price=PriceFeatures(median_30d=52.0, median_volume_30d=40))
    signal = ANALYZER.evaluate(fs, _ctx())[0]
    assert signal.evidence["fair_value"] == 52.0
    assert signal.evidence["fair_value_source"] == "median_30d"


def test_a_small_absolute_margin_is_suppressed():
    # 15p item, ask 13, fair value 15: 13% under, but under the 10-plat floor
    fs = _fs(price=PriceFeatures(median_7d=15.0, median_volume_30d=40), online_best_ask=13, online_best_bid=12)
    assert ANALYZER.evaluate(fs, _ctx()) == []


def test_a_small_percentage_margin_is_suppressed():
    assert ANALYZER.evaluate(_fs(online_best_ask=46, online_best_bid=44), _ctx()) == []


def test_a_thin_online_book_kills_the_flip():
    assert ANALYZER.evaluate(_fs(online_ask_depth=(), online_ask_count=0), _ctx()) == []


def test_one_unit_under_a_wall_is_suppressed_when_below_the_quantity_floor():
    ctx = _ctx(min_quantity_at_best=3)
    assert ANALYZER.evaluate(_fs(online_ask_depth=(2, 40)), ctx) == []
    assert ANALYZER.evaluate(_fs(online_ask_depth=(4, 40)), ctx) != []


def test_a_stale_book_kills_the_flip():
    assert ANALYZER.evaluate(_fs(stale_share=0.9), _ctx()) == []


def test_an_illiquid_item_kills_the_flip():
    fs = _fs(price=PriceFeatures(median_7d=50.0, median_volume_30d=1))
    assert ANALYZER.evaluate(fs, _ctx()) == []


def test_missing_blocks_produce_nothing_rather_than_an_exception():
    empty = FeatureSet(slug="x", rank=0, ts=NOW)
    assert ANALYZER.evaluate(empty, _ctx()) == []


def test_no_online_ask_produces_nothing():
    assert ANALYZER.evaluate(_fs(online_best_ask=None), _ctx()) == []


def test_confidence_rises_with_margin_and_volume():
    # both clear the 10-plat and 0.15 floors; the first is the weaker edge
    weak = ANALYZER.evaluate(_fs(online_best_ask=39), _ctx())
    strong = ANALYZER.evaluate(_fs(online_best_ask=20), _ctx())
    assert 0 < weak[0].confidence < strong[0].confidence <= 1.0
