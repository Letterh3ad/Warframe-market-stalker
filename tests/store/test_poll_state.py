from datetime import datetime, timedelta, timezone

from wfm.store.poll_state import PollStateRepo

NOW = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)


def test_round_trip(conn):
    repo = PollStateRepo(conn)
    repo.upsert(
        slug="loki_prime_set",
        rank=0,
        due_at=NOW + timedelta(minutes=30),
        interval_minutes=15.0,
        unchanged_polls=2,
        last_polled_at=NOW,
    )
    state = repo.get("loki_prime_set", 0)
    assert state["due_at"] == NOW + timedelta(minutes=30)
    assert state["due_at"].tzinfo is not None
    assert state["last_polled_at"] == NOW
    assert state["interval_minutes"] == 15.0
    assert state["unchanged_polls"] == 2


def test_get_on_a_missing_row_returns_none(conn):
    assert PollStateRepo(conn).get("nope", 0) is None


def test_last_polled_at_is_optional(conn):
    repo = PollStateRepo(conn)
    repo.upsert(slug="x", rank=0, due_at=NOW, interval_minutes=15.0, unchanged_polls=0)
    assert repo.get("x", 0)["last_polled_at"] is None


def test_upsert_replaces_rather_than_duplicates(conn):
    repo = PollStateRepo(conn)
    repo.upsert(slug="x", rank=0, due_at=NOW, interval_minutes=15.0, unchanged_polls=0)
    repo.upsert(
        slug="x",
        rank=0,
        due_at=NOW + timedelta(hours=1),
        interval_minutes=30.0,
        unchanged_polls=1,
    )
    assert conn.execute("SELECT COUNT(*) FROM poll_state").fetchone()[0] == 1
    state = repo.get("x", 0)
    assert state["interval_minutes"] == 30.0
    assert state["unchanged_polls"] == 1
    assert state["due_at"] == NOW + timedelta(hours=1)


def test_two_ranks_of_the_same_slug_are_independent(conn):
    repo = PollStateRepo(conn)
    repo.upsert(slug="x", rank=0, due_at=NOW, interval_minutes=15.0, unchanged_polls=0)
    repo.upsert(
        slug="x", rank=1, due_at=NOW + timedelta(hours=2), interval_minutes=60.0, unchanged_polls=0
    )
    assert repo.get("x", 0)["interval_minutes"] == 15.0
    assert repo.get("x", 1)["interval_minutes"] == 60.0


def test_all_keys_on_slug_and_rank(conn):
    repo = PollStateRepo(conn)
    repo.upsert(slug="x", rank=0, due_at=NOW, interval_minutes=15.0, unchanged_polls=0)
    repo.upsert(slug="y", rank=2, due_at=NOW, interval_minutes=15.0, unchanged_polls=0)
    assert set(repo.all().keys()) == {("x", 0), ("y", 2)}


def test_delete_reports_whether_a_row_was_removed(conn):
    repo = PollStateRepo(conn)
    repo.upsert(slug="x", rank=0, due_at=NOW, interval_minutes=15.0, unchanged_polls=0)
    assert repo.delete("x", 0) is True
    assert repo.delete("x", 0) is False


def test_delete_removes_the_row_from_all(conn):
    repo = PollStateRepo(conn)
    repo.upsert(slug="x", rank=0, due_at=NOW, interval_minutes=15.0, unchanged_polls=0)
    repo.upsert(slug="y", rank=0, due_at=NOW, interval_minutes=15.0, unchanged_polls=0)
    repo.delete("x", 0)
    assert set(repo.all().keys()) == {("y", 0)}


def test_due_at_survives_a_fresh_repo_over_the_same_connection(conn):
    PollStateRepo(conn).upsert(slug="x", rank=0, due_at=NOW, interval_minutes=15.0, unchanged_polls=0)
    reopened = PollStateRepo(conn)
    assert reopened.get("x", 0)["due_at"] == NOW
