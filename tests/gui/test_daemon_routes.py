import os
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.daemon import control
from wfm.gui.app import build_app
from wfm.services.context import AppContext

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _client(conn, config: Config) -> TestClient:
    ctx = AppContext(config, conn=conn, clock=FakeClock(start_utc=NOW))
    client = TestClient(build_app(ctx))
    client.ctx = ctx
    return client


def test_status_of_a_daemon_that_never_ran(conn, tmp_path):
    client = _client(conn, Config(pid_file=tmp_path / "wfm.pid"))
    assert client.get("/daemon/status").json()["status"] == "not running"


def test_stop_without_a_running_daemon_says_so(conn, tmp_path):
    client = _client(conn, Config(pid_file=tmp_path / "wfm.pid"))
    assert client.post("/daemon/stop").json()["stopped"] is False


def test_stop_flips_a_running_daemon_to_stopping(conn, tmp_path):
    client = _client(conn, Config(pid_file=tmp_path / "wfm.pid"))
    control.write_pid(client.ctx.config.pid_file, os.getpid())
    client.ctx.daemon_state.mark_started(pid=os.getpid(), when=NOW)

    response = client.post("/daemon/stop")

    assert response.json()["stopped"] is True
    assert client.ctx.daemon_state.get()["status"] == "stopping"
