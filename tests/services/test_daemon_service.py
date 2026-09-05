import asyncio
import os
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from tests.fakes.api import StubClient
from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.daemon import control
from wfm.daemon.runner import Daemon
from wfm.models import DailyCandle, Item
from wfm.services import daemon_service
from wfm.services.context import AppContext
from wfm.sync.budget import Priority

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


async def test_scan_once_polls_at_interactive_priority(ctx):
    # wfm scan is a foreground CLI request, not the daemon's own background poll
    # loop: it must get INTERACTIVE queue precedence, or a user running it while
    # the daemon is up queues behind background polls with no precedence at all.
    await daemon_service.scan_once(ctx)
    priorities = [priority for url, priority in ctx.new_client().calls if "/orders/item/" in url]
    assert priorities == [Priority.INTERACTIVE]


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


# --- start(): single-instance guard and pid-file cleanup, both rate-bearing ---

async def test_start_refuses_a_second_instance(ctx):
    # Guards the 3.0 req/s hard ceiling: two daemons against the same watchlist
    # would double the request rate against it.
    control.write_pid(ctx.config.pid_file, os.getpid())

    result = await daemon_service.start(ctx)

    assert result == {"started": False, "reason": f"already running as pid {os.getpid()}"}


def _daily_work_already_done(ctx) -> None:
    """START is 10:00, past both sweep_hour and digest_hour, so a bounded run would
    otherwise spend its one iteration on the daily tasks instead of a poll."""
    ctx.daemon_state.mark_started(pid=1, when=START)
    ctx.daemon_state.mark_daily_done("sweep", START.date())
    ctx.daemon_state.mark_daily_done("digest", START.date())


def _bounded_daemon(monkeypatch, iterations: int = 1, on_run=None):
    """start() runs the loop to completion, so tests need a bounded run. Bounding it
    here rather than pre-setting a stop flag: the flag is exactly what start() now
    clears, so it can no longer double as a way to end the run."""

    class BoundedDaemon(Daemon):
        async def run(self, max_iterations=None):
            if on_run is not None:
                on_run()
            return await super().run(max_iterations=iterations)

    monkeypatch.setattr(daemon_service, "Daemon", BoundedDaemon)


async def test_start_clears_the_pid_file_once_the_run_ends(ctx, monkeypatch):
    _daily_work_already_done(ctx)
    _bounded_daemon(monkeypatch)

    result = await daemon_service.start(ctx)

    assert result["started"] is True
    assert control.read_pid(ctx.config.pid_file) is None


async def test_start_clears_a_stale_stopping_flag_instead_of_reporting_a_phantom_start(
    ctx, monkeypatch
):
    """I4: a daemon killed before it consumed its own stop leaves status='stopping'.
    run() reads that before mark_started() clears it, so the next `wfm daemon start`
    returned {"started": True, "polls": 0} and exited without polling anything."""
    _daily_work_already_done(ctx)
    ctx.daemon_state.request_stop(when=START)
    seen = {}
    _bounded_daemon(
        monkeypatch, on_run=lambda: seen.update(stop_flag=ctx.daemon_state.stop_requested())
    )

    result = await daemon_service.start(ctx)

    assert seen["stop_flag"] is False, "the stale flag is cleared before the run starts"
    assert result["polls"] == 1, "the daemon actually ran rather than exiting at once"
    assert result["reason"] is None


async def test_start_force_clears_an_orphaned_pid_file(ctx, monkeypatch):
    """PID recycling can point an orphaned pid file at an unrelated live process,
    which the single-instance guard then reads as a running daemon forever."""
    _daily_work_already_done(ctx)
    control.write_pid(ctx.config.pid_file, os.getpid())
    _bounded_daemon(monkeypatch)

    result = await daemon_service.start(ctx, force=True)

    assert result["started"] is True
    assert result["polls"] == 1


async def test_start_without_force_still_refuses_a_live_pid_file(ctx):
    control.write_pid(ctx.config.pid_file, os.getpid())

    result = await daemon_service.start(ctx)

    assert result["started"] is False


# --- start(): optional embedded GUI server ---


class _FakeServer:
    def __init__(self, config):
        self.config = config
        self.should_exit = False
        self.served = False
        self.started = False

    async def serve(self):
        self.served = True
        self.started = True
        while not self.should_exit:
            await asyncio.sleep(0)


class _FakeServerThatFailsToStart:
    """Simulates a bind failure: uvicorn.Server.serve() raises SystemExit before
    ever setting started=True."""

    def __init__(self, config):
        self.config = config
        self.should_exit = False
        self.started = False

    async def serve(self):
        raise SystemExit(1)


async def test_start_with_serve_gui_runs_a_uvicorn_server_alongside_the_daemon(ctx, monkeypatch):
    _daily_work_already_done(ctx)
    _bounded_daemon(monkeypatch)
    fake_servers = []
    fake_app = object()

    def fake_server(config):
        server = _FakeServer(config)
        fake_servers.append(server)
        return server

    monkeypatch.setattr("wfm.services.daemon_service.uvicorn.Server", fake_server)

    result = await daemon_service.start(ctx, serve_gui=True, app=fake_app)

    assert result["started"] is True
    assert fake_servers[0].config.app is fake_app
    assert fake_servers[0].served is True
    assert fake_servers[0].should_exit is True
    assert "gui_error" not in result


async def test_start_without_serve_gui_never_touches_uvicorn(ctx, monkeypatch):
    _daily_work_already_done(ctx)
    _bounded_daemon(monkeypatch)

    def boom(*args, **kwargs):
        raise AssertionError("uvicorn.Server must not be constructed when serve_gui=False")

    monkeypatch.setattr("wfm.services.daemon_service.uvicorn.Server", boom)

    result = await daemon_service.start(ctx)

    assert result["started"] is True


async def test_start_with_serve_gui_requires_an_app(ctx, monkeypatch):
    _daily_work_already_done(ctx)
    _bounded_daemon(monkeypatch)

    with pytest.raises(ValueError):
        await daemon_service.start(ctx, serve_gui=True)


async def test_start_surfaces_a_gui_bind_failure_instead_of_crashing(ctx, monkeypatch):
    _daily_work_already_done(ctx)
    _bounded_daemon(monkeypatch)
    monkeypatch.setattr(
        "wfm.services.daemon_service.uvicorn.Server", _FakeServerThatFailsToStart
    )

    result = await daemon_service.start(ctx, serve_gui=True, app=object())

    assert result["started"] is True
    assert "gui_error" in result
    assert "1" in result["gui_error"]
