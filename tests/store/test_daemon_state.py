from datetime import date, datetime, timedelta, timezone

import pytest

from wfm.store.daemon_state import DaemonStateRepo
from wfm.store.migrate import SCHEMA_VERSION, current_version

NOW = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)


def test_the_third_migration_applied(conn):
    assert current_version(conn) == SCHEMA_VERSION == 3


def test_start_heartbeat_stop(conn):
    repo = DaemonStateRepo(conn)
    assert repo.get() is None
    repo.mark_started(pid=4321, when=NOW)
    assert repo.get()["pid"] == 4321
    assert repo.get()["status"] == "running"

    repo.heartbeat(when=NOW + timedelta(minutes=1), status="running", detail="polled 12 items")
    state = repo.get()
    assert state["detail"] == "polled 12 items"
    assert state["heartbeat_at"] > state["started_at"]

    repo.mark_stopped(when=NOW + timedelta(hours=2), detail="sigterm")
    assert repo.get()["status"] == "stopped"


def test_there_is_only_ever_one_row(conn):
    repo = DaemonStateRepo(conn)
    repo.mark_started(pid=1, when=NOW)
    repo.mark_started(pid=2, when=NOW + timedelta(seconds=1))
    assert conn.execute("SELECT COUNT(*) FROM daemon_state").fetchone()[0] == 1
    assert repo.get()["pid"] == 2


def test_a_halted_status_records_its_reason(conn):
    repo = DaemonStateRepo(conn)
    repo.mark_started(pid=1, when=NOW)
    repo.heartbeat(when=NOW, status="halted", detail="circuit breaker: 3 consecutive 429s")
    assert "429" in repo.get()["detail"]


def test_request_stop_on_a_fresh_database_returns_false_and_writes_nothing(conn):
    repo = DaemonStateRepo(conn)
    assert repo.request_stop(when=NOW) is False
    assert repo.get() is None


def test_request_stop_on_a_running_daemon_flips_status_and_is_observed(conn):
    repo = DaemonStateRepo(conn)
    repo.mark_started(pid=1, when=NOW)
    assert repo.stop_requested() is False
    assert repo.request_stop(when=NOW + timedelta(seconds=1)) is True
    assert repo.get()["status"] == "stopping"
    assert repo.stop_requested() is True


def test_request_stop_on_an_already_stopped_daemon_returns_false(conn):
    repo = DaemonStateRepo(conn)
    repo.mark_started(pid=1, when=NOW)
    repo.mark_stopped(when=NOW + timedelta(minutes=1))
    assert repo.request_stop(when=NOW + timedelta(minutes=2)) is False


def test_mark_started_clears_a_stale_stopping_flag(conn):
    repo = DaemonStateRepo(conn)
    repo.mark_started(pid=1, when=NOW)
    repo.request_stop(when=NOW + timedelta(seconds=1))
    assert repo.stop_requested() is True

    repo.mark_started(pid=2, when=NOW + timedelta(seconds=2))
    assert repo.stop_requested() is False


def test_mark_daily_done_rejects_an_unknown_kind(conn):
    repo = DaemonStateRepo(conn)
    repo.mark_started(pid=1, when=NOW)
    with pytest.raises(ValueError):
        repo.mark_daily_done("brunch", date(2026, 8, 27), when=NOW)


def test_daily_done_is_none_until_marked(conn):
    repo = DaemonStateRepo(conn)
    repo.mark_started(pid=1, when=NOW)
    assert repo.daily_done("sweep") is None
    assert repo.daily_done("digest") is None


def test_mark_daily_done_records_its_when_as_the_heartbeat(conn):
    repo = DaemonStateRepo(conn)
    repo.mark_started(pid=1, when=NOW)
    finished_at = NOW + timedelta(hours=3)
    repo.mark_daily_done("digest", date(2026, 8, 27), when=finished_at)
    assert repo.get()["heartbeat_at"] == finished_at


def test_daily_done_survives_a_restart(conn):
    repo = DaemonStateRepo(conn)
    repo.mark_started(pid=1, when=NOW)
    repo.mark_daily_done("sweep", date(2026, 8, 27), when=NOW)
    assert repo.daily_done("sweep") == date(2026, 8, 27)
    assert repo.daily_done("digest") is None

    repo.mark_started(pid=1, when=NOW + timedelta(hours=1))
    assert repo.daily_done("sweep") == date(2026, 8, 27)
