from dataclasses import replace
from datetime import date, datetime, timezone

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


def test_market_context_reads_the_injected_clock_not_the_wall_clock(ctx):
    """The anchor is capped at now, so a clock set before the data cannot read candles
    that had not happened yet. This is what keeps the block deterministic in tests.
    """
    before_the_data = feature_service.market_context(
        ctx, days=7, now=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    )
    assert before_the_data.median_return is None
    assert feature_service.market_context(ctx, days=7, now=NOW).median_return is not None


def test_market_context_anchors_on_the_newest_candle_not_on_today(ctx):
    """Daily statistics cover complete days only, so the newest candle is yesterday.
    Anchoring the window on today costs it a day, and a 7 day return needs 8 points,
    so every item returns None and the whole market block goes quietly empty.
    """
    a_day_after_the_data_ends = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    context = feature_service.market_context(ctx, days=7, now=a_day_after_the_data_ends)
    assert context.median_return is not None


def test_the_market_sample_spreads_across_the_catalog_instead_of_taking_the_head(conn):
    """all_slugs() is alphabetical, so taking the first N samples only the "a" items.
    That misreports the market median and hands cohort_size 0 to every tag that happens
    to sort late, which silently disables the cohort comparison.
    """
    context = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    context.items.upsert_many(
        [
            Item(slug=f"item_{i:02d}", name=f"I{i}", url_name=f"item_{i:02d}",
                 tags=("early",) if i < 5 else ("late",))
            for i in range(10)
        ]
    )
    context.daily.upsert_many(
        [
            DailyCandle(slug=f"item_{i:02d}", rank=0, date=f"2026-08-{d:02d}",
                        close=40 + d, median=40 + d, volume=5)
            for i in range(10)
            for d in range(20, 28)
        ]
    )
    sampled = feature_service.market_context(context, days=7, sample_limit=5, now=NOW)
    assert sampled.cohort_sizes.get("late", 0) > 0


@pytest.mark.parametrize("size", [501, 900, 999, 1000, 3839])
def test_the_market_sample_never_degenerates_into_the_head_slice(size):
    """A stride of len // limit is 1 for any catalog between limit+1 and 2*limit-1, which
    silently reduces the sample to the alphabetical head it exists to avoid. 900 items is
    the shape of a partly synced catalog.
    """
    slugs = [f"s{i:05d}" for i in range(size)]
    sample = feature_service._spread(slugs, 500)
    assert len(sample) == 500
    assert len(set(sample)) == 500, "picks must be distinct"
    # spanning the list is the property that matters: the head slice fails this for every
    # catalog larger than the limit, which is what made the market read the "a" items.
    assert sample[-1] >= slugs[int(len(slugs) * 0.9)]


def test_the_market_sample_handles_catalogs_at_or_below_the_limit():
    assert feature_service._spread(["a", "b"], 500) == ["a", "b"]
    assert feature_service._spread([], 500) == []


def test_build_for_carries_the_market_block_when_a_context_is_supplied(ctx):
    context = feature_service.market_context(ctx, days=7)
    fs = feature_service.build_for(ctx, "x", 0, market=context, now=NOW)
    assert fs.has("market") is True
    assert fs.market.tag == "mod"


def test_the_item_return_uses_the_contexts_anchor_not_a_freshly_derived_one(ctx):
    """report_group builds the market context once and reuses it per member. If a sync
    lands a newer candle mid-run, re-deriving the anchor per call would measure the
    item's own return over a different window than its peers and misattribute a
    market-wide move to the item.
    """
    context = feature_service.market_context(ctx, days=7, now=NOW)
    assert context.anchor == date(2026, 8, 27)
    before = feature_service.build_for(ctx, "x", 0, market=context, now=NOW)

    # a mid-run sync writes tomorrow's candle for x only
    ctx.daily.upsert_many(
        [DailyCandle(slug="x", rank=0, date="2026-08-28", close=999,
                     high=1000, low=998, median=999, volume=20)]
    )
    after = feature_service.build_for(
        ctx, "x", 0, market=context,
        now=datetime(2026, 8, 28, 0, 1, tzinfo=timezone.utc),
    )
    assert after.market.excess_return_7d == before.market.excess_return_7d


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
