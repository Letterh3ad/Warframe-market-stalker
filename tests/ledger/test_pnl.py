from datetime import datetime, timedelta, timezone

import pytest

from wfm.ledger import pnl
from wfm.models import Side, Trade

NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _t(side, qty, plat, day=0, slug="x", rank=0) -> Trade:
    return Trade(slug=slug, rank=rank, ts=NOW + timedelta(days=day), side=side,
                 quantity=qty, platinum=plat, note=None)


def test_realized_matches_lots_first_in_first_out():
    trades = [
        _t(Side.BUY, 2, 40, day=0),
        _t(Side.BUY, 2, 60, day=1),
        _t(Side.SELL, 3, 80, day=2),
    ]
    lots = pnl.realized(trades)
    assert [(lot["quantity"], lot["cost"], lot["proceeds"]) for lot in lots] == [
        (2, 80, 160),
        (1, 60, 80),
    ]
    assert sum(lot["profit"] for lot in lots) == 100


def test_realized_is_empty_without_sells():
    assert pnl.realized([_t(Side.BUY, 2, 40)]) == []


def test_a_sale_beyond_the_held_quantity_is_matched_only_up_to_what_was_bought():
    lots = pnl.realized([_t(Side.BUY, 1, 40), _t(Side.SELL, 5, 50)])
    assert sum(lot["quantity"] for lot in lots) == 1


def test_lots_never_cross_items_or_ranks():
    trades = [
        _t(Side.BUY, 1, 40, slug="a"),
        _t(Side.BUY, 1, 400, slug="b"),
        _t(Side.SELL, 1, 50, slug="a"),
    ]
    lots = pnl.realized(trades)
    assert len(lots) == 1
    assert lots[0]["slug"] == "a"
    assert lots[0]["profit"] == 10


def test_ranks_are_separate_positions():
    trades = [
        _t(Side.BUY, 1, 40, rank=0),
        _t(Side.BUY, 1, 400, rank=10),
        _t(Side.SELL, 1, 450, rank=10),
    ]
    lots = pnl.realized(trades)
    assert lots[0]["rank"] == 10
    assert lots[0]["profit"] == 50


def test_summary_totals_realized_profit_and_groups_by_item():
    trades = [_t(Side.BUY, 2, 40), _t(Side.SELL, 2, 55)]
    summary = pnl.summary(trades)
    assert summary["realized_profit"] == 30
    assert summary["by_item"][("x", 0)]["profit"] == 30
    assert summary["trades"] == 2


def test_unrealized_uses_the_supplied_marks():
    rows = pnl.unrealized([("x", 0, 4, 40.0)], marks={("x", 0): 55.0})
    assert rows[0]["unrealized_profit"] == pytest.approx(60.0)


def test_unrealized_reports_none_when_there_is_no_mark():
    rows = pnl.unrealized([("x", 0, 4, 40.0)], marks={})
    assert rows[0]["mark"] is None
    assert rows[0]["unrealized_profit"] is None


def test_cost_basis_uses_only_the_fifo_remainder_not_the_blended_average():
    trades = [
        _t(Side.BUY, 2, 30, day=0),
        _t(Side.BUY, 2, 50, day=1),
        _t(Side.SELL, 1, 90, day=2),
    ]
    basis = pnl.cost_basis(trades)
    # Blended average over all buys would be 40.0. The sale closes the first unit of
    # the 30-cost lot first (FIFO), leaving 1@30 and 2@50: (30 + 100) / 3.
    assert basis[("x", 0)] == pytest.approx((30 + 100) / 3)


def test_cost_basis_omits_a_fully_closed_position():
    trades = [_t(Side.BUY, 1, 40), _t(Side.SELL, 1, 60)]
    assert pnl.cost_basis(trades) == {}
