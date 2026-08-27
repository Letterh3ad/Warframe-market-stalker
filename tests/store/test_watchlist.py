from datetime import datetime, timezone

from wfm.store.watchlist import WatchlistRepo

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def test_add_and_list(conn):
    repo = WatchlistRepo(conn)
    repo.add("primed_continuity", 10, NOW, pin_weight=2.0)
    repo.add("mirage_prime_set", 0, NOW)
    entries = repo.all()
    assert {(e.slug, e.rank) for e in entries} == {
        ("primed_continuity", 10),
        ("mirage_prime_set", 0),
    }
    assert repo.get("primed_continuity", 10).pin_weight == 2.0
    assert repo.get("primed_continuity", 10).alert_override is False


def test_the_same_slug_may_be_watched_at_two_ranks(conn):
    repo = WatchlistRepo(conn)
    repo.add("primed_continuity", 0, NOW)
    repo.add("primed_continuity", 10, NOW)
    assert len(repo.all()) == 2


def test_add_twice_keeps_the_newer_settings(conn):
    repo = WatchlistRepo(conn)
    repo.add("x", 0, NOW, pin_weight=1.0)
    repo.add("x", 0, NOW, pin_weight=5.0)
    assert len(repo.all()) == 1
    assert repo.get("x", 0).pin_weight == 5.0


def test_remove_reports_whether_anything_went(conn):
    repo = WatchlistRepo(conn)
    repo.add("x", 0, NOW)
    assert repo.remove("x", 0) is True
    assert repo.remove("x", 0) is False


def test_alert_override_toggles(conn):
    repo = WatchlistRepo(conn)
    repo.add("x", 0, NOW)
    repo.set_alert_override("x", 0, True)
    assert repo.get("x", 0).alert_override is True
