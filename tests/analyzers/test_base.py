from datetime import datetime, timezone

from wfm.analyzers.base import Context, Holding

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def test_holding_lookup_is_rank_aware():
    ctx = Context(
        now=NOW,
        holdings={("x", 10): Holding("x", 10, quantity=3, avg_cost=41.5)},
        watchlist={},
        thresholds={},
    )
    assert ctx.holding_for("x", 10).quantity == 3
    assert ctx.holding_for("x", 0) is None


def test_thresholds_for_returns_an_empty_mapping_when_unconfigured():
    ctx = Context(now=NOW, holdings={}, watchlist={}, thresholds={"flip": {"min_margin_plat": 12}})
    assert ctx.thresholds_for("flip") == {"min_margin_plat": 12}
    assert ctx.thresholds_for("revert") == {}


def test_context_fields_default_to_empty():
    ctx = Context(now=NOW)
    assert ctx.holdings == {} and ctx.watchlist == {} and ctx.thresholds == {}
