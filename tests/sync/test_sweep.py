from datetime import datetime, timezone

import pytest

from tests.fakes.api import StubClient
from tests.fakes.clock import FakeClock
from wfm.api.errors import ApiError, CircuitOpen
from wfm.models import Item
from wfm.store.items import ItemsRepo
from wfm.store.stats import DailyStatsRepo, HourlyStatsRepo
from wfm.store.sweep import SweepStateRepo
from wfm.sync.budget import Priority
from wfm.sync.sweep import SWEEP_NAME, run_sweep

START = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)

STATS = {
    "payload": {
        "statistics_closed": {
            "90days": [
                {
                    "datetime": "2026-08-26T00:00:00.000+00:00",
                    "volume": 10,
                    "min_price": 35,
                    "max_price": 55,
                    "open_price": 40,
                    "closed_price": 44,
                    "avg_price": 43.0,
                    "median": 42,
                    "mod_rank": 0,
                }
            ],
            "48hours": [],
        }
    }
}


def _seed(conn, slugs: list[str]):
    items = ItemsRepo(conn)
    items.upsert_many([Item(slug=s, name=s, url_name=s) for s in slugs])
    return items, DailyStatsRepo(conn), HourlyStatsRepo(conn), SweepStateRepo(conn)


async def test_sweep_walks_the_catalog_in_slug_order(conn):
    items, daily, hourly, sweeps = _seed(conn, ["a", "b", "c"])
    client = StubClient({"/statistics": STATS})
    result = await run_sweep(client, items, daily, hourly, sweeps, FakeClock(start_utc=START))
    assert result.processed == 3
    assert [url.split("/")[-2] for url, _ in client.calls] == ["a", "b", "c"]
    assert sweeps.get(SWEEP_NAME)["status"] == "done"


async def test_sweep_requests_are_bulk_priority(conn):
    items, daily, hourly, sweeps = _seed(conn, ["a"])
    client = StubClient({"/statistics": STATS})
    await run_sweep(client, items, daily, hourly, sweeps, FakeClock(start_utc=START))
    assert client.calls[0][1] is Priority.BULK


async def test_checkpoints_after_every_item(conn):
    items, daily, hourly, sweeps = _seed(conn, ["a", "b", "c"])
    client = StubClient({"/statistics": STATS})
    seen: list[tuple[str, int]] = []
    await run_sweep(
        client, items, daily, hourly, sweeps, FakeClock(start_utc=START),
        on_progress=lambda slug, n: seen.append((slug, n)),
    )
    assert seen == [("a", 1), ("b", 2), ("c", 3)]


async def test_a_halted_sweep_resumes_after_its_cursor(conn):
    items, daily, hourly, sweeps = _seed(conn, ["a", "b", "c", "d"])
    sweeps.start(SWEEP_NAME, START)
    sweeps.checkpoint(SWEEP_NAME, cursor="b", when=START, done_count=2)
    client = StubClient({"/statistics": STATS})
    result = await run_sweep(client, items, daily, hourly, sweeps, FakeClock(start_utc=START))
    assert result.resumed_from == "b"
    assert [url.split("/")[-2] for url, _ in client.calls] == ["c", "d"]


async def test_a_completed_sweep_starts_over_from_the_beginning(conn):
    items, daily, hourly, sweeps = _seed(conn, ["a", "b"])
    client = StubClient({"/statistics": STATS})
    await run_sweep(client, items, daily, hourly, sweeps, FakeClock(start_utc=START))
    client = StubClient({"/statistics": STATS})
    result = await run_sweep(client, items, daily, hourly, sweeps, FakeClock(start_utc=START))
    assert result.processed == 2
    assert result.resumed_from is None


async def test_a_tripped_breaker_halts_and_records_the_reason(conn):
    items, daily, hourly, sweeps = _seed(conn, ["a", "b", "c"])
    client = StubClient({"/statistics": STATS}, errors={"/b/": CircuitOpen("3 consecutive 429s")})
    result = await run_sweep(client, items, daily, hourly, sweeps, FakeClock(start_utc=START))
    assert result.halted is True
    assert "429" in result.reason
    state = sweeps.get(SWEEP_NAME)
    assert state["status"] == "halted"
    assert state["cursor"] == "a"


async def test_a_single_item_error_is_skipped_not_fatal(conn):
    items, daily, hourly, sweeps = _seed(conn, ["a", "b", "c"])
    client = StubClient({"/statistics": STATS}, errors={"/b/": ApiError("404")})
    result = await run_sweep(client, items, daily, hourly, sweeps, FakeClock(start_utc=START))
    assert result.halted is False
    assert result.processed == 3
    assert daily.latest_date("c", 0) == "2026-08-26"


async def test_limit_stops_early_and_leaves_a_resumable_cursor(conn):
    items, daily, hourly, sweeps = _seed(conn, ["a", "b", "c"])
    client = StubClient({"/statistics": STATS})
    result = await run_sweep(
        client, items, daily, hourly, sweeps, FakeClock(start_utc=START), limit=2
    )
    assert result.processed == 2
    assert sweeps.get(SWEEP_NAME)["cursor"] == "b"
    assert sweeps.get(SWEEP_NAME)["status"] == "running"
