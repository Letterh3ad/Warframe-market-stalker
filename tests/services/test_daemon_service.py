import os
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from tests.fakes.api import StubClient
from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.daemon import control
from wfm.models import DailyCandle, Item
from wfm.services import daemon_service
from wfm.services.context import AppContext

START = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
ORDERS = [
    {"platinum": 45, "quantity": 2, "rank": 0, "type": "sell", "visible": True,
     "user": {"status": "ingame"}, "updatedAt": "2026-08-27T09:00:00Z"},
]


@pytest.fixture
def ctx(conn, tmp_path):
    client = StubClient({"/orders/item/": ORDERS})
    config = replace(Config(), pid_file=tmp_path / "wfm.pid")
    context = AppContext(config, conn=conn, clock=FakeClock(start_utc=START), client=client)
    context.items.upsert_many([Item(slug="a", name="A", url_name="a")])
    context.daily.upsert_many(
        [DailyCandle(slug="a", rank=0, date=f"2026-06-{d:02d}", close=50, high=52, low=48,
                     median=50, volume=30) for d in range(1, 31)]
    )
    context.watchlist.add("a", 0, START)
    return context


def test_status_of_a_daemon_that_never_ran(ctx):
    assert daemon_service.status(ctx)["status"] == "not running"


def test_status_reports_a_stale_heartbeat(ctx):
    ctx.daemon_state.mark_started(pid=1, when=START)
    ctx.clock.advance(60 * 60)
    status = daemon_service.status(ctx)
    assert status["heartbeat_age_seconds"] >= 3600
    assert status["stale"] is True


def test_stop_without_a_running_daemon_says_so(ctx):
    assert daemon_service.stop(ctx)["stopped"] is False


async def test_scan_once_polls_every_watchlist_item_and_returns_results(ctx):
    result = await daemon_service.scan_once(ctx)
    assert result["polled"] == 1
    assert ctx.orders.latest("a", 0) is not None
    assert "signals" in result


async def test_scan_once_of_a_single_slug(ctx):
    ctx.items.upsert_many([Item(slug="b", name="B", url_name="b")])
    ctx.watchlist.add("b", 0, START)
    result = await daemon_service.scan_once(ctx, slug="a")
    assert result["polled"] == 1


# --- addendum: stop()/status() over the daemon_state "stopping" flag, not os.kill ---

def test_stop_with_a_live_pid_and_running_daemon_row_flips_to_stopping(ctx):
    control.write_pid(ctx.config.pid_file, os.getpid())
    ctx.daemon_state.mark_started(pid=os.getpid(), when=START)

    result = daemon_service.stop(ctx)

    assert result["stopped"] is True
    assert result["pid"] == os.getpid()
    assert ctx.daemon_state.get()["status"] == "stopping"


def test_stop_with_a_dead_pid_clears_the_pid_file_and_does_not_touch_daemon_state(ctx):
    control.write_pid(ctx.config.pid_file, 9_999_999)
    ctx.daemon_state.mark_started(pid=9_999_999, when=START)

    result = daemon_service.stop(ctx)

    assert result == {"stopped": False, "reason": "no running daemon"}
    assert control.read_pid(ctx.config.pid_file) is None
    assert ctx.daemon_state.get()["status"] == "running"


def test_status_of_a_stopping_daemon_reports_stopping(ctx):
    ctx.daemon_state.mark_started(pid=os.getpid(), when=START)
    ctx.daemon_state.request_stop(when=START)
    assert daemon_service.status(ctx)["status"] == "stopping"
