from datetime import datetime, timezone

from wfm.models import Side, Trade
from wfm.store.trades import TradesRepo

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _trade(side: Side, qty: int, plat: int) -> Trade:
    return Trade(slug="x", rank=0, ts=NOW, side=side, quantity=qty, platinum=plat)


def test_record_and_read_back(conn):
    repo = TradesRepo(conn)
    trade_id = repo.record(_trade(Side.BUY, 3, 40))
    trades = repo.all_for("x", 0)
    assert trades[0].id == trade_id
    assert trades[0].side is Side.BUY
    assert trades[0].quantity == 3


def test_holdings_view_nets_buys_against_sells(conn):
    repo = TradesRepo(conn)
    repo.record(_trade(Side.BUY, 4, 40))
    repo.record(_trade(Side.BUY, 2, 46))
    repo.record(_trade(Side.SELL, 1, 60))
    assert repo.holdings() == [("x", 0, 5, 42.0)]


def test_fully_sold_positions_leave_holdings(conn):
    repo = TradesRepo(conn)
    repo.record(_trade(Side.BUY, 2, 40))
    repo.record(_trade(Side.SELL, 2, 60))
    assert repo.holdings() == []
