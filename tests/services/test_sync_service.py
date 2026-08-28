from datetime import datetime, timezone

import pytest

from tests.fakes.api import StubClient
from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.services import sync_service
from wfm.services.context import AppContext

START = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)

VERSIONS = {"collections": {"items": "v42"}}
ITEMS = [{"slug": "a", "i18n": {"en": {"name": "A"}}, "tags": ["mod"], "maxRank": 10}]
STATS = {
    "payload": {
        "statistics_closed": {
            "90days": [
                {
                    "datetime": "2026-08-26T00:00:00.000+00:00",
                    "volume": 10, "min_price": 35, "max_price": 55, "open_price": 40,
                    "closed_price": 44, "avg_price": 43.0, "median": 42, "mod_rank": 0,
                }
            ],
            "48hours": [],
        }
    }
}


@pytest.fixture
def ctx(conn):
    client = StubClient({"/versions": VERSIONS, "/items": ITEMS, "/statistics": STATS})
    context = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=START), client=client)
    return context


async def test_sync_reports_what_changed(ctx):
    result = await sync_service.sync(ctx)
    assert result["changed"] is True
    assert result["item_count"] == 1
    assert result["requests_spent"] == 2


async def test_dry_run_spends_nothing_and_says_so(ctx):
    result = await sync_service.sync(ctx, dry_run=True)
    assert result["dry_run"] is True
    assert result["requests_spent"] == 0
    assert ctx.items.count() == 0


async def test_backfill_of_a_single_slug(ctx):
    await sync_service.sync(ctx)
    result = await sync_service.backfill(ctx, slug="a")
    assert result["processed"] == 1
    assert ctx.daily.latest_date("a", 0) == "2026-08-26"


async def test_backfill_all_walks_the_catalog(ctx):
    await sync_service.sync(ctx)
    result = await sync_service.backfill(ctx)
    assert result["processed"] == 1
    assert result["halted"] is False


async def test_status_reports_sweep_progress(ctx):
    await sync_service.sync(ctx)
    await sync_service.backfill(ctx)
    status = sync_service.status(ctx)
    assert status["backfill"]["status"] == "done"
    assert status["catalog"]["status"] == "done"
    assert status["items"] == 1
