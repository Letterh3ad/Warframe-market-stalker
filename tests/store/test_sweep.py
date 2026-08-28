from datetime import datetime, timedelta, timezone

import pytest

from wfm.models import SweepStatus
from wfm.store.sweep import SweepStateRepo

NOW = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)


def test_start_then_checkpoint_then_finish(conn):
    repo = SweepStateRepo(conn)
    assert repo.get("backfill") is None
    repo.start("backfill", NOW)
    assert repo.get("backfill")["status"] == "running"
    repo.checkpoint("backfill", cursor="mirage_prime_set", when=NOW, done_count=120)
    state = repo.get("backfill")
    assert state["cursor"] == "mirage_prime_set"
    assert state["done_count"] == 120
    repo.finish("backfill", NOW + timedelta(minutes=21))
    assert repo.get("backfill")["status"] == "done"


def test_halt_records_the_reason(conn):
    repo = SweepStateRepo(conn)
    repo.start("backfill", NOW)
    repo.halt("backfill", reason="circuit breaker: 3 consecutive 429s", when=NOW)
    state = repo.get("backfill")
    assert state["status"] == "halted"
    assert "429" in state["reason"]


def test_restart_preserves_the_cursor_for_resumption(conn):
    repo = SweepStateRepo(conn)
    repo.start("backfill", NOW)
    repo.checkpoint("backfill", cursor="k", when=NOW, done_count=5)
    repo.start("backfill", NOW + timedelta(days=1))
    assert repo.get("backfill")["cursor"] == "k"


def test_checkpoint_on_an_unknown_sweep_raises(conn):
    with pytest.raises(KeyError):
        SweepStateRepo(conn).checkpoint("typo", "cursor", NOW, 1)


def test_finish_on_an_unknown_sweep_raises(conn):
    with pytest.raises(KeyError):
        SweepStateRepo(conn).finish("typo", NOW)


def test_halt_records_a_sweep_that_never_started(conn):
    repo = SweepStateRepo(conn)
    repo.halt("catalog", "rate limited", NOW)
    state = repo.get("catalog")
    assert state["status"] == SweepStatus.HALTED.value
    assert state["reason"] == "rate limited"
