from datetime import datetime, timezone

import pytest

from wfm.analyzers.base import Context
from wfm.analyzers.set_arbitrage import ANALYZER
from wfm.features.types import BookFeatures, FeatureSet, Provenance
from wfm.models import Direction, Horizon, Scope

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
THRESHOLDS = dict(ANALYZER.DEFAULTS)


def _ctx(**overrides) -> Context:
    return Context(now=NOW, thresholds={"set_arbitrage": {**THRESHOLDS, **overrides}})


def _fs(slug: str, ask: int | None, bid: int | None) -> FeatureSet:
    return FeatureSet(
        slug=slug, rank=0, ts=NOW,
        book=BookFeatures(online_best_ask=ask, online_best_bid=bid),
        provenance=Provenance(available=frozenset({"book"})),
    )


def test_metadata():
    assert ANALYZER.name == "set_arbitrage"
    assert ANALYZER.horizon is Horizon.URGENT
    assert ANALYZER.scope is Scope.GROUP
    assert ANALYZER.required_features() == {"book"}


def test_a_cheap_set_fires_a_buy_signal_anchored_on_the_set():
    fss = [
        _fs("frame_prime_set", ask=80, bid=70),
        _fs("frame_prime_chassis_blueprint", ask=None, bid=40),
        _fs("frame_prime_systems_blueprint", ask=None, bid=60),
    ]
    signals = ANALYZER.evaluate(fss, _ctx())
    assert len(signals) == 1
    signal = signals[0]
    assert signal.slug == "frame_prime_set"
    assert signal.rank == 0
    assert signal.direction is Direction.BUY
    assert signal.magnitude == pytest.approx(20.0)
    assert signal.evidence["margin_plat"] == pytest.approx(20.0)
    assert signal.evidence["margin_pct"] == pytest.approx(0.25)


def test_an_expensive_set_fires_a_sell_signal():
    fss = [
        _fs("frame_prime_set", ask=140, bid=150),
        _fs("frame_prime_chassis_blueprint", ask=40, bid=None),
        _fs("frame_prime_systems_blueprint", ask=60, bid=None),
    ]
    signals = ANALYZER.evaluate(fss, _ctx())
    assert len(signals) == 1
    signal = signals[0]
    assert signal.direction is Direction.SELL
    assert signal.magnitude == pytest.approx(50.0)


def test_a_group_with_no_set_member_produces_nothing():
    fss = [_fs("frame_prime_chassis_blueprint", ask=40, bid=30)]
    assert ANALYZER.evaluate(fss, _ctx()) == []


def test_a_group_with_two_set_members_produces_nothing():
    fss = [_fs("frame_prime_set", ask=80, bid=70), _fs("weapon_prime_set", ask=50, bid=40)]
    assert ANALYZER.evaluate(fss, _ctx()) == []


def test_a_set_alone_with_no_parts_produces_nothing():
    assert ANALYZER.evaluate([_fs("frame_prime_set", ask=80, bid=70)], _ctx()) == []


def test_a_missing_price_on_one_part_skips_only_the_leg_that_needs_it():
    # The BUY leg only needs bids, so a part missing its ask does not stop it firing;
    # the SELL leg (which needs every ask) is the one silently skipped.
    fss = [
        _fs("frame_prime_set", ask=80, bid=90),
        _fs("frame_prime_chassis_blueprint", ask=None, bid=50),
        _fs("frame_prime_systems_blueprint", ask=200, bid=60),
    ]
    signals = ANALYZER.evaluate(fss, _ctx())
    assert len(signals) == 1
    assert signals[0].direction is Direction.BUY
    assert signals[0].magnitude == pytest.approx(30.0)


def test_a_margin_below_threshold_is_suppressed():
    fss = [
        _fs("frame_prime_set", ask=95, bid=90),
        _fs("frame_prime_chassis_blueprint", ask=None, bid=50),
        _fs("frame_prime_systems_blueprint", ask=None, bid=48),
    ]
    assert ANALYZER.evaluate(fss, _ctx()) == []


def test_missing_book_on_the_set_or_a_part_produces_nothing():
    no_book_set = FeatureSet(slug="frame_prime_set", rank=0, ts=NOW)
    part = _fs("frame_prime_chassis_blueprint", ask=None, bid=40)
    assert ANALYZER.evaluate([no_book_set, part], _ctx()) == []

    set_fs = _fs("frame_prime_set", ask=80, bid=70)
    no_book_part = FeatureSet(slug="frame_prime_chassis_blueprint", rank=0, ts=NOW)
    assert ANALYZER.evaluate([set_fs, no_book_part], _ctx()) == []


def test_confidence_rises_with_margin():
    weak_fss = [
        _fs("frame_prime_set", ask=150, bid=140),
        _fs("frame_prime_chassis_blueprint", ask=None, bid=80),
        _fs("frame_prime_systems_blueprint", ask=None, bid=80),
    ]
    strong_fss = [
        _fs("frame_prime_set", ask=100, bid=90),
        _fs("frame_prime_chassis_blueprint", ask=None, bid=80),
        _fs("frame_prime_systems_blueprint", ask=None, bid=80),
    ]
    weak = ANALYZER.evaluate(weak_fss, _ctx())
    strong = ANALYZER.evaluate(strong_fss, _ctx())
    assert 0 < weak[0].confidence < strong[0].confidence <= 1.0
