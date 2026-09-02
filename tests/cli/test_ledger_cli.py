import json
from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.cli.main import main
from wfm.config import Config
from wfm.models import Direction, Horizon, Item, Signal
from wfm.services.context import AppContext

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


@pytest.fixture
def wired(conn, monkeypatch):
    ctx = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    ctx.items.upsert_many([Item(slug="x", name="Ex Item", url_name="x")])
    monkeypatch.setattr("wfm.cli.context_factory.build", lambda args: ctx)
    return ctx


def test_trade_buy_then_holdings(wired, capsys):
    assert main(["trade", "buy", "x", "3", "40"]) == 0
    capsys.readouterr()
    assert main(["--json", "holdings"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["quantity"] == 3


def test_trade_sell_beyond_holdings_exits_one(wired, capsys):
    assert main(["trade", "sell", "x", "3", "60"]) == 1
    assert "hold" in capsys.readouterr().err.lower()


def test_pnl_reports_realized(wired, capsys):
    main(["trade", "buy", "x", "2", "40"])
    main(["trade", "sell", "x", "2", "70"])
    capsys.readouterr()
    assert main(["--json", "pnl"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["realized_profit"] == 60


def test_signals_renders_stored_signals(wired, capsys):
    wired.signals.insert(
        Signal(slug="x", rank=0, analyzer="revert", ts=NOW, direction=Direction.BUY,
               magnitude=2.0, confidence=0.8, horizon=Horizon.DAILY, evidence={"robust_z": -2.0})
    )
    assert main(["signals"]) == 0
    out = capsys.readouterr().out
    assert "Ex Item" in out
    assert "robust_z" in out


def test_signals_filters(wired, capsys):
    assert main(["--json", "signals", "--analyzer", "flip"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_signals_since_bad_input_exits_one(wired, capsys):
    assert main(["signals", "--since", "not-a-date"]) == 1
    assert "since" in capsys.readouterr().err.lower()


def test_pnl_since_bad_input_exits_one(wired, capsys):
    assert main(["pnl", "--since", "garbage"]) == 1
    assert "since" in capsys.readouterr().err.lower()


def test_digest_runs_and_marks_pending_daily_signals(wired, capsys):
    wired.signals.insert(
        Signal(slug="x", rank=0, analyzer="revert", ts=NOW, direction=Direction.BUY,
               magnitude=2.0, confidence=0.8, horizon=Horizon.DAILY, evidence={"robust_z": -2.0})
    )
    assert main(["digest"]) == 0
    assert "Daily digest: 1 signals" in capsys.readouterr().out
    assert wired.signals.query()[0].alerted_at is not None
