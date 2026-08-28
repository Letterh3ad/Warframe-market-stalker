from datetime import datetime, timezone

import pytest

from wfm.store.groups import GroupsRepo

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def test_create_and_get(conn):
    repo = GroupsRepo(conn)
    group = repo.create("primes", NOW)
    assert group.id is not None
    assert repo.get("primes").name == "primes"
    assert repo.get("absent") is None


def test_duplicate_name_raises(conn):
    repo = GroupsRepo(conn)
    repo.create("primes", NOW)
    with pytest.raises(ValueError):
        repo.create("primes", NOW)


def test_members_are_rank_aware_and_independent_of_the_watchlist(conn):
    repo = GroupsRepo(conn)
    repo.create("mods", NOW)
    repo.add_member("mods", "primed_continuity", 10)
    repo.add_member("mods", "primed_continuity", 0)
    assert repo.members("mods") == [("primed_continuity", 0), ("primed_continuity", 10)]
    assert conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 0


def test_delete_removes_members(conn):
    repo = GroupsRepo(conn)
    repo.create("mods", NOW)
    repo.add_member("mods", "x", 0)
    assert repo.delete("mods") is True
    assert conn.execute("SELECT COUNT(*) FROM group_members").fetchone()[0] == 0
    assert repo.delete("mods") is False


def test_add_member_to_a_missing_group_raises(conn):
    with pytest.raises(KeyError):
        GroupsRepo(conn).add_member("nope", "x", 0)


def test_create_reports_a_unique_violation_as_valueerror(conn, monkeypatch):
    """A blind get() stands in for the TOCTOU race: a concurrent create wins between
    the existence check and the insert, so the UNIQUE violation is the only signal."""
    repo = GroupsRepo(conn)
    repo.create("dupe", NOW)
    monkeypatch.setattr(GroupsRepo, "get", lambda self, name: None)
    with pytest.raises(ValueError):
        repo.create("dupe", NOW)
