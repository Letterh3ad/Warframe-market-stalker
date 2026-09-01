from datetime import datetime, timezone

from tests.fakes.api import StubClient
from tests.fakes.clock import FakeClock
from wfm.models import DailyCandle
from wfm.store.stats import DailyStatsRepo, HourlyStatsRepo
from wfm.sync.backfill import backfill_item

START = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)


def _stats(dates: list[str], ranks: tuple[int, ...] = (0,)) -> dict:
    return {
        "payload": {
            "statistics_closed": {
                "90days": [
                    {
                        "datetime": f"{d}T00:00:00.000+00:00",
                        "volume": 10,
                        "min_price": 35,
                        "max_price": 55,
                        "open_price": 40,
                        "closed_price": 44,
                        "avg_price": 43.0,
                        "median": 42,
                        "mod_rank": rank,
                    }
                    for d in dates
                    for rank in ranks
                ],
                "48hours": [
                    {
                        "datetime": "2026-08-27T09:00:00.000+00:00",
                        "volume": 2,
                        "min_price": 41,
                        "max_price": 46,
                        "open_price": 42,
                        "closed_price": 45,
                        "avg_price": 43.5,
                        "median": 43,
                        "mod_rank": 0,
                    }
                ],
            }
        }
    }


async def test_first_run_stores_every_candle(conn):
    daily, hourly = DailyStatsRepo(conn), HourlyStatsRepo(conn)
    client = StubClient({"/statistics": _stats(["2026-08-24", "2026-08-25", "2026-08-26"])})
    result = await backfill_item(client, "x", daily, hourly, FakeClock(start_utc=START))
    assert result.daily_written == 3
    assert result.hourly_written == 1
    assert daily.latest_date("x", 0) == "2026-08-26"


async def test_second_run_writes_only_newer_candles(conn):
    daily, hourly = DailyStatsRepo(conn), HourlyStatsRepo(conn)
    clock = FakeClock(start_utc=START)
    client = StubClient({"/statistics": _stats(["2026-08-24", "2026-08-25"])})
    await backfill_item(client, "x", daily, hourly, clock)
    client = StubClient({"/statistics": _stats(["2026-08-24", "2026-08-25", "2026-08-26"])})
    result = await backfill_item(client, "x", daily, hourly, clock)
    assert result.daily_written == 1
    assert daily.latest_date("x", 0) == "2026-08-26"


async def test_ranks_are_stored_independently(conn):
    daily, hourly = DailyStatsRepo(conn), HourlyStatsRepo(conn)
    client = StubClient({"/statistics": _stats(["2026-08-26"], ranks=(0, 10))})
    result = await backfill_item(client, "x", daily, hourly, FakeClock(start_utc=START))
    assert result.daily_written == 2
    assert daily.ranks_for("x") == [0, 10]


async def test_an_item_with_no_trade_history_is_not_an_error(conn):
    daily, hourly = DailyStatsRepo(conn), HourlyStatsRepo(conn)
    client = StubClient({"/statistics": {"payload": {"statistics_closed": {}}}})
    result = await backfill_item(client, "x", daily, hourly, FakeClock(start_utc=START))
    assert result.daily_written == 0
    assert result.skipped is False


async def test_incremental_run_with_nothing_new_writes_nothing(conn):
    daily, hourly = DailyStatsRepo(conn), HourlyStatsRepo(conn)
    clock = FakeClock(start_utc=START)
    payload = _stats(["2026-08-26"])
    await backfill_item(client=StubClient({"/statistics": payload}), slug="x",
                        daily_repo=daily, hourly_repo=hourly, clock=clock)
    result = await backfill_item(client=StubClient({"/statistics": payload}), slug="x",
                                 daily_repo=daily, hourly_repo=hourly, clock=clock)
    assert result.daily_written == 0
