from datetime import datetime, timedelta, timezone

from wfm.models import BookSnapshot
from wfm.store.orders import OrderSnapshotsRepo, RawSnapshotsRepo

TS = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)


def _snap(ts: datetime, ask: int) -> BookSnapshot:
    return BookSnapshot(
        slug="x", rank=0, ts=ts, best_bid=30, best_ask=ask,
        online_best_bid=28, online_best_ask=ask + 2,
        bid_depth=(1, 3, 6, 9, 12), ask_depth=(2, 4, 7, 11, 14),
        bid_count=40, ask_count=61, online_bid_count=5, online_ask_count=9,
        stale_share=0.31,
    )


def test_insert_and_latest_round_trip(conn):
    repo = OrderSnapshotsRepo(conn)
    repo.insert(_snap(TS, 45))
    got = repo.latest("x", 0)
    assert got.best_ask == 45
    assert got.bid_depth == (1, 3, 6, 9, 12)
    assert got.stale_share == 0.31
    assert got.ts == TS


def test_online_depth_curves_round_trip(conn):
    repo = OrderSnapshotsRepo(conn)
    ts = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    repo.insert(
        BookSnapshot(
            slug="x", rank=0, ts=ts,
            bid_depth=(3, 7), ask_depth=(2, 5),
            online_bid_depth=(2, 4), online_ask_depth=(1,),
        )
    )
    got = repo.latest("x", 0)
    assert got.online_bid_depth == (2, 4)
    assert got.online_ask_depth == (1,)


def test_latest_returns_the_newest(conn):
    repo = OrderSnapshotsRepo(conn)
    repo.insert(_snap(TS, 45))
    repo.insert(_snap(TS + timedelta(minutes=30), 41))
    assert repo.latest("x", 0).best_ask == 41


def test_recent_is_newest_first_and_capped(conn):
    repo = OrderSnapshotsRepo(conn)
    for i in range(5):
        repo.insert(_snap(TS + timedelta(minutes=30 * i), 45 - i))
    assert [s.best_ask for s in repo.recent("x", 0, limit=3)] == [41, 42, 43]


def test_computed_spreads():
    snap = _snap(TS, 45)
    assert snap.spread == 15
    assert snap.online_spread == 19


def test_raw_sampling_stores_one_in_n(conn):
    repo = RawSnapshotsRepo(conn)
    stored = [
        repo.maybe_store("x", 0, TS + timedelta(minutes=i), "{}", sample_rate=10)
        for i in range(30)
    ]
    assert sum(stored) == 3
    assert repo.count() == 3


def test_raw_sampling_disabled_when_rate_is_zero(conn):
    repo = RawSnapshotsRepo(conn)
    assert repo.maybe_store("x", 0, TS, "{}", sample_rate=0) is False
    assert repo.count() == 0


def test_sampling_holds_across_fresh_repo_instances(conn):
    stored = sum(
        RawSnapshotsRepo(conn).maybe_store("x", 0, TS + timedelta(minutes=i), "{}", sample_rate=10)
        for i in range(200)
    )
    assert 0 < stored < 60
    assert RawSnapshotsRepo(conn).count() == stored
