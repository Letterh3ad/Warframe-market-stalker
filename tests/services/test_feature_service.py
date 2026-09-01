from dataclasses import replace
from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.features.book import summarize
from wfm.models import DailyCandle, HourlyCandle, Item, Order, Side
from wfm.services import feature_service
from wfm.services.context import AppContext

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _seed(context: AppContext) -> AppContext:
    context.items.upsert_many(
        [
            Item(slug="x", name="X", url_name="x", tags=("mod",)),
            Item(slug="peer", name="Peer", url_name="peer", tags=("mod",)),
        ]
    )
    context.daily.upsert_many(
        [
            DailyCandle(slug="x", rank=0, date=f"2026-08-{d:02d}", close=40 + (d % 5),
                        high=46, low=36, median=42, volume=20)
            for d in range(1, 28)
        ]
        + [
            DailyCandle(slug="peer", rank=0, date=f"2026-08-{d:02d}", close=100,
                        high=101, low=99, median=100, volume=5)
            for d in range(1, 28)
        ]
    )
    context.hourly.upsert_many(
        [HourlyCandle(slug="x", rank=0, ts=NOW.replace(hour=h), close=42, volume=3)
         for h in range(0, 12)]
    )
    return context


@pytest.fixture
def ctx(conn):
    return _seed(AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW)))


def test_build_for_without_a_book_marks_book_unavailable(ctx):
    fs = feature_service.build_for(ctx, "x", 0, now=NOW)
    assert fs.has("price") is True
    assert fs.has("book") is False
    assert fs.book is None
    assert fs.price.median_7d is not None
    assert fs.provenance.samples["price_90d"] == 27


def test_build_for_with_a_book_includes_book_features(ctx):
    snapshot = summarize(
        [
            Order(platinum=45, quantity=1, rank=0, side=Side.SELL, user_status="ingame",
                  updated_at=NOW),
            Order(platinum=40, quantity=2, rank=0, side=Side.BUY, user_status="online",
                  updated_at=NOW),
        ],
        slug="x", rank=0, ts=NOW,
    )
    fs = feature_service.build_for(ctx, "x", 0, snapshot=snapshot, now=NOW)
    assert fs.has("book") is True
    assert fs.book.online_best_ask == 45
    assert fs.provenance.samples["book"] == 2


def test_market_context_is_built_from_stored_series(ctx):
    context = feature_service.market_context(ctx, days=7)
    assert context.median_return is not None
    assert "mod" in context.tag_returns


def test_market_context_reads_the_clock_not_the_wall_clock(ctx):
    """The window must end at the injected now. Reading real wall-clock time here makes
    the market block quietly empty whenever the fixtures age past the window.
    """
    stale = feature_service.market_context(ctx, days=7, now=datetime(2027, 1, 1,
                                                                    tzinfo=timezone.utc))
    assert stale.median_return is None
    assert feature_service.market_context(ctx, days=7, now=NOW).median_return is not None


def test_build_for_carries_the_market_block_when_a_context_is_supplied(ctx):
    context = feature_service.market_context(ctx, days=7)
    fs = feature_service.build_for(ctx, "x", 0, market=context, now=NOW)
    assert fs.has("market") is True
    assert fs.market.tag == "mod"


def test_an_item_with_no_history_produces_an_advertised_empty_set(ctx):
    fs = feature_service.build_for(ctx, "unknown", 0, now=NOW)
    assert fs.has("price") is False
    assert fs.provenance.samples["price_90d"] == 0


def test_persist_writes_nothing_unless_the_flag_is_set(ctx):
    fs = feature_service.build_for(ctx, "x", 0, now=NOW)
    feature_service.persist(ctx, fs)
    assert _feature_rows(ctx) == 0


def test_persist_writes_when_the_flag_is_set(conn):
    ctx = _seed(
        AppContext(
            replace(Config(), persist_features=True), conn=conn, clock=FakeClock(start_utc=NOW)
        )
    )
    fs = feature_service.build_for(ctx, "x", 0, now=NOW)
    feature_service.persist(ctx, fs)
    assert _feature_rows(ctx) == 1


def test_the_persisted_payload_is_json_a_later_reader_can_load(conn):
    import json

    ctx = _seed(
        AppContext(
            replace(Config(), persist_features=True), conn=conn, clock=FakeClock(start_utc=NOW)
        )
    )
    feature_service.persist(ctx, feature_service.build_for(ctx, "x", 0, now=NOW))
    payload = ctx.conn.execute("SELECT payload_json FROM features").fetchone()[0]
    assert json.loads(payload)["slug"] == "x"


def _feature_rows(ctx) -> int:
    return ctx.conn.execute("SELECT COUNT(*) FROM features").fetchone()[0]
