from datetime import datetime, timezone

import pytest

from tests.fakes.api import StubClient
from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.models import DailyCandle, Item
from wfm.services import report_service
from wfm.services.context import AppContext
from wfm.sync.budget import Priority

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

ORDERS = [
    {"platinum": 45, "quantity": 1, "rank": 0, "type": "sell", "visible": True,
     "user": {"status": "ingame"}, "updatedAt": "2026-08-27T09:00:00Z"},
    {"platinum": 44, "quantity": 2, "rank": 0, "type": "sell", "visible": True,
     "user": {"status": "offline"}, "updatedAt": "2024-01-01T09:00:00Z"},
    {"platinum": 40, "quantity": 3, "rank": 0, "type": "buy", "visible": True,
     "user": {"status": "online"}, "updatedAt": "2026-08-27T08:00:00Z"},
]


@pytest.fixture
def ctx(conn):
    client = StubClient({"/orders/item/": ORDERS})
    context = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW), client=client)
    context.items.upsert_many([Item(slug="x", name="Ex Item", url_name="x", tags=("mod",))])
    context.daily.upsert_many(
        [
            DailyCandle(slug="x", rank=0, date=f"2026-08-{d:02d}", close=40 + (d % 5),
                        high=46, low=36, median=42, volume=20)
            for d in range(1, 28)
        ]
    )
    return context


async def test_poll_book_stores_a_snapshot(ctx):
    snapshot = await report_service.poll_book(ctx, "x", 0)
    assert snapshot.online_best_ask == 45
    stored = ctx.orders.latest("x", 0)
    assert stored.best_ask == 44
    assert stored.online_best_ask == 45


async def test_report_without_refresh_makes_no_request(ctx):
    result = await report_service.report(ctx, "x")
    assert result["slug"] == "x"
    assert result["book"] is None
    assert ctx.new_client().calls == []


async def test_report_with_refresh_uses_interactive_priority(ctx):
    result = await report_service.report(ctx, "x", refresh=True)
    assert result["book"]["online_best_ask"] == 45
    assert ctx.new_client().calls[0][1] is Priority.INTERACTIVE


async def test_report_falls_back_to_the_last_stored_snapshot(ctx):
    await report_service.poll_book(ctx, "x", 0)
    result = await report_service.report(ctx, "x")
    assert result["book"]["online_best_ask"] == 45
    assert result["book_age_seconds"] == 0


async def test_report_includes_price_features_and_provenance(ctx):
    result = await report_service.report(ctx, "x")
    assert result["price"]["median_7d"] is not None
    assert "price" in result["provenance"]["available"]


async def test_report_of_an_unknown_item_raises_lookup_error(ctx):
    with pytest.raises(LookupError):
        await report_service.report(ctx, "nothing_like_this")


async def test_report_group_returns_one_entry_per_member(ctx):
    ctx.groups.create("mods", NOW)
    ctx.groups.add_member("mods", "x", 0)
    result = await report_service.report_group(ctx, "mods")
    assert result["name"] == "mods"
    assert len(result["items"]) == 1
    assert result["items"][0]["slug"] == "x"


async def test_report_reports_the_age_of_a_stale_stored_book(ctx):
    await report_service.poll_book(ctx, "x", 0)
    ctx.clock.advance(3600)
    result = await report_service.report(ctx, "x")
    assert result["book_age_seconds"] == 3600


async def test_report_group_builds_the_market_context_once_for_the_whole_group(ctx):
    """Rebuilding it per member costs a full sampled catalog pass each time, so a 20
    member group runs thousands of queries for a figure the module says moves slowly.
    """
    ctx.items.upsert_many(
        [Item(slug=f"m{i}", name=f"M{i}", url_name=f"m{i}", tags=("mod",)) for i in range(3)]
    )
    ctx.groups.create("mods", NOW)
    for i in range(3):
        ctx.groups.add_member("mods", f"m{i}", 0)

    calls = []
    original = report_service.feature_service.market_context
    ctx_module = report_service.feature_service

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    ctx_module.market_context = counting
    try:
        result = await report_service.report_group(ctx, "mods")
    finally:
        ctx_module.market_context = original

    assert len(result["items"]) == 3
    assert len(calls) == 1, f"built the market context {len(calls)} times for 3 members"


async def test_report_refuses_rank_all_rather_than_silently_reporting_one_rank(ctx):
    ctx.items.upsert_many(
        [Item(slug="modded", name="Modded", url_name="modded", max_rank=10, canonical_rank=10)]
    )
    with pytest.raises(ValueError, match="rank"):
        await report_service.report(ctx, "modded", rank="all")
