import json
from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.cli.main import main
from wfm.config import Config
from wfm.models import DailyCandle, Item
from wfm.services.context import AppContext

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


@pytest.fixture
def wired(conn, monkeypatch):
    ctx = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    ctx.items.upsert_many([Item(slug="x", name="Ex Item", url_name="x")])
    ctx.daily.upsert_many(
        [DailyCandle(slug="x", rank=0, date=f"2026-08-{d:02d}", close=40, high=42, low=38,
                     median=40, volume=9) for d in range(1, 27)]
    )
    monkeypatch.setattr("wfm.cli.context_factory.build", lambda args: ctx)
    return ctx


def test_report_prints_a_human_summary(wired, capsys):
    assert main(["report", "x"]) == 0
    out = capsys.readouterr().out
    assert "Ex Item" in out
    assert "median" in out.lower()


def test_report_json_is_parseable(wired, capsys):
    assert main(["--json", "report", "x"]) == 0
    assert json.loads(capsys.readouterr().out)["slug"] == "x"


def test_report_of_an_unknown_item_exits_one(wired, capsys):
    assert main(["report", "nope"]) == 1
    assert "nope" in capsys.readouterr().err


def test_report_with_no_target_exits_two(wired, capsys):
    assert main(["report"]) == 2
    assert "--group" in capsys.readouterr().err


def test_report_closes_the_context_when_done(wired, capsys, monkeypatch):
    closed = []
    monkeypatch.setattr(wired, "aclose", lambda: closed.append(True) or _noop())
    assert main(["--json", "report", "x"]) == 0
    assert closed == [True]


async def _noop() -> None:
    return None
