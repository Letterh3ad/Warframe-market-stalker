from datetime import datetime, timedelta, timezone

import pytest

from wfm.models import DailyCandle, HourlyCandle
from wfm.store.stats import DailyStatsRepo, HourlyStatsRepo


def _daily(date: str, close: float, rank: int = 0) -> DailyCandle:
    return DailyCandle(slug="x", rank=rank, date=date, volume=5, close=close, median=close)


def test_daily_upsert_is_idempotent_on_the_key(conn):
    repo = DailyStatsRepo(conn)
    repo.upsert_many([_daily("2026-08-01", 40)])
    repo.upsert_many([_daily("2026-08-01", 44)])
    rows = repo.window("x", 0, days=30, end="2026-08-02")
    assert len(rows) == 1
    assert rows[0].close == 44


def test_daily_window_is_ordered_and_bounded(conn):
    repo = DailyStatsRepo(conn)
    repo.upsert_many([_daily(f"2026-08-{d:02d}", 40 + d) for d in range(1, 11)])
    rows = repo.window("x", 0, days=3, end="2026-08-10")
    assert [r.date for r in rows] == ["2026-08-08", "2026-08-09", "2026-08-10"]


def test_ranks_are_stored_separately(conn):
    repo = DailyStatsRepo(conn)
    repo.upsert_many([_daily("2026-08-01", 40, rank=0), _daily("2026-08-01", 400, rank=10)])
    assert repo.window("x", 10, days=5, end="2026-08-01")[0].close == 400
    assert repo.ranks_for("x") == [0, 10]


def test_latest_date_drives_incremental_backfill(conn):
    repo = DailyStatsRepo(conn)
    assert repo.latest_date("x", 0) is None
    repo.upsert_many([_daily("2026-08-01", 40), _daily("2026-08-03", 41)])
    assert repo.latest_date("x", 0) == "2026-08-03"


def test_hourly_prune_drops_old_rows(conn):
    repo = HourlyStatsRepo(conn)
    now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    repo.upsert_many(
        [HourlyCandle(slug="x", rank=0, ts=now - timedelta(days=d), close=40) for d in range(20)]
    )
    assert repo.prune(before=now - timedelta(days=14)) == 5
    assert len(repo.window("x", 0, hours=24 * 30)) == 15


def test_hourly_upsert_rejects_naive_datetime(conn):
    repo = HourlyStatsRepo(conn)
    with pytest.raises(ValueError):
        repo.upsert_many([HourlyCandle(slug="x", rank=0, ts=datetime(2026, 8, 27, 12), close=40)])


def test_hourly_upsert_collapses_the_same_instant_across_offsets(conn):
    repo = HourlyStatsRepo(conn)
    utc_ts = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    plus_two_ts = utc_ts.astimezone(timezone(timedelta(hours=2)))
    repo.upsert_many([HourlyCandle(slug="x", rank=0, ts=utc_ts, close=40)])
    repo.upsert_many([HourlyCandle(slug="x", rank=0, ts=plus_two_ts, close=41)])
    rows = repo.window("x", 0, hours=24)
    assert len(rows) == 1
    assert rows[0].close == 41
