from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.gui.app import build_app
from wfm.models import Item, Side, Trade
from wfm.services.context import AppContext

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _client(conn) -> TestClient:
    ctx = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    ctx.items.upsert_many([Item(slug="x", name="X", url_name="x")])
    client = TestClient(build_app(ctx))
    client.ctx = ctx
    return client


def test_holdings_reflects_recorded_trades(conn):
    client = _client(conn)
    client.ctx.trades.record(
        Trade(slug="x", rank=0, ts=NOW, side=Side.BUY, quantity=3, platinum=40)
    )

    response = client.get("/ledger/holdings")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["slug"] == "x"
    assert body[0]["quantity"] == 3


def test_pnl_reports_realized_profit_after_a_sell(conn):
    client = _client(conn)
    client.ctx.trades.record(
        Trade(slug="x", rank=0, ts=NOW, side=Side.BUY, quantity=3, platinum=40)
    )
    client.ctx.trades.record(
        Trade(slug="x", rank=0, ts=NOW, side=Side.SELL, quantity=1, platinum=55)
    )

    response = client.get("/ledger/pnl")

    assert response.status_code == 200
    assert response.json()["realized_profit"] == 15
