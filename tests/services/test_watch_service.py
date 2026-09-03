from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.models import DailyCandle, Item
from wfm.services import watch_service
from wfm.services.context import AppContext

START = datetime(2026, 8, 27, tzinfo=timezone.utc)


@pytest.fixture
def ctx(conn):
    context = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=START))
    context.items.upsert_many(
        [
            Item(slug="hot", name="Hot Item", url_name="hot"),
            Item(slug="cold", name="Cold Item", url_name="cold"),
            Item(slug="ranked", name="Ranked Mod", url_name="ranked", max_rank=10,
                 canonical_rank=10),
        ]
    )
    context.daily.upsert_many(
        [DailyCandle(slug="hot", rank=0, date=f"2026-08-{d:02d}", volume=200,
                     close=40 + (d % 5) * 6, high=60, low=30, median=44)
         for d in range(1, 27)]
        + [DailyCandle(slug="cold", rank=0, date=f"2026-08-{d:02d}", volume=1,
                       close=40, high=41, low=39, median=40)
           for d in range(1, 27)]
    )
    return context


def test_add_defaults_to_the_canonical_rank(ctx):
    result = watch_service.add(ctx, "ranked")
    assert result["slug"] == "ranked"
    assert result["ranks"] == [10]
    assert ctx.watchlist.get("ranked", 10) is not None


def test_add_with_rank_all_adds_every_rank(ctx):
    result = watch_service.add(ctx, "ranked", rank="all")
    assert len(result["ranks"]) == 11
    assert len(ctx.watchlist.all()) == 11


def test_add_is_idempotent_and_updates_pin(ctx):
    watch_service.add(ctx, "hot", pin=1.0)
    watch_service.add(ctx, "hot", pin=3.0)
    entries = watch_service.list_(ctx)
    assert len(entries) == 1
    assert entries[0]["pin_weight"] == 3.0


def test_remove_reports_missing_entries(ctx):
    watch_service.add(ctx, "hot")
    assert watch_service.remove(ctx, "hot")["removed"] == 1
    assert watch_service.remove(ctx, "hot")["removed"] == 0


def test_suggest_ranks_by_volume_and_volatility_and_never_auto_adds(ctx):
    suggestions = watch_service.suggest(ctx, top=2)
    assert suggestions[0]["slug"] == "hot"
    assert suggestions[0]["score"] > suggestions[-1]["score"]
    assert ctx.watchlist.all() == []


def test_suggest_excludes_already_watched_items(ctx):
    watch_service.add(ctx, "hot")
    assert "hot" not in {s["slug"] for s in watch_service.suggest(ctx, top=5)}


def test_suggest_anchors_on_the_injected_clock_not_wall_clock(conn):
    """Old code called DailyStatsRepo.window with no end=, so it anchored on real wall
    time. A clock set to a date far from the actual system clock, with candles dated
    to match the clock, only produces suggestions if suggest() actually reads ctx.clock.
    """
    context = AppContext(
        Config(), conn=conn, clock=FakeClock(start_utc=datetime(2019, 6, 25, tzinfo=timezone.utc))
    )
    context.items.upsert_many([Item(slug="old", name="Old Item", url_name="old")])
    context.daily.upsert_many(
        [DailyCandle(slug="old", rank=0, date=f"2019-06-{d:02d}", volume=200,
                     close=40 + (d % 5) * 6, high=60, low=30, median=44)
         for d in range(1, 16)]
    )
    suggestions = watch_service.suggest(context, top=5)
    assert [s["slug"] for s in suggestions] == ["old"]
