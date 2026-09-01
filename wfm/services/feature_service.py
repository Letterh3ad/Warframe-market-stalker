from __future__ import annotations

import json
from datetime import datetime

from wfm.features import book as book_features
from wfm.features import market as market_features
from wfm.features import price as price_features
from wfm.features import seasonality as seasonality_features
from wfm.features.market import MarketContext
from wfm.features.types import FeatureSet, Provenance
from wfm.models import BookSnapshot
from wfm.services.context import AppContext

PRICE_WINDOW_DAYS = 90
HOURLY_WINDOW_HOURS = 24 * 14


def market_context(
    ctx: AppContext, days: int = 7, sample_limit: int = 500, now: datetime | None = None
) -> MarketContext:
    """Sampled rather than exhaustive: a full catalog pass on every tick would dominate
    the tick cost for a figure that moves slowly.
    """
    end = (now or ctx.clock.utcnow()).date().isoformat()
    slugs = ctx.items.all_slugs()[:sample_limit]
    series = {}
    tags: dict[str, tuple[str, ...]] = {}
    for slug in slugs:
        item = ctx.items.get(slug)
        if item is None:
            continue
        candles = ctx.daily.window(slug, item.canonical_rank, days=days + 1, end=end)
        if candles:
            series[slug] = candles
            tags[slug] = item.tags
    return market_features.build_context(series, tags, days=days)


def build_for(
    ctx: AppContext,
    slug: str,
    rank: int,
    snapshot: BookSnapshot | None = None,
    market: MarketContext | None = None,
    now: datetime | None = None,
) -> FeatureSet:
    now = now or ctx.clock.utcnow()
    samples: dict[str, int] = {}
    available: set[str] = set()

    candles = ctx.daily.window(slug, rank, days=PRICE_WINDOW_DAYS, end=now.date().isoformat())
    price_block, price_samples = price_features.build(candles)
    samples.update(price_samples)
    if candles:
        available.add("price")

    book_block = None
    if snapshot is not None:
        book_block, book_samples = book_features.build(snapshot)
        samples.update(book_samples)
        available.add("book")

    hourly = ctx.hourly.window(slug, rank, hours=HOURLY_WINDOW_HOURS)
    seasonality_block, seasonality_samples = seasonality_features.build(hourly, now=now)
    samples.update(seasonality_samples)
    if hourly:
        available.add("seasonality")

    market_block = None
    if market is not None:
        item = ctx.items.get(slug)
        market_block, market_samples = market_features.build(
            candles, tags=item.tags if item else (), context=market
        )
        samples.update(market_samples)
        available.add("market")

    return FeatureSet(
        slug=slug,
        rank=rank,
        ts=now,
        price=price_block if candles else None,
        book=book_block,
        seasonality=seasonality_block if hourly else None,
        market=market_block,
        provenance=Provenance(samples=samples, available=frozenset(available)),
    )


def persist(ctx: AppContext, fs: FeatureSet) -> None:
    """Debug only. Nothing in the production path reads this table."""
    if not ctx.config.persist_features:
        return
    ctx.conn.execute(
        'INSERT OR REPLACE INTO features (slug, "rank", ts, payload_json) VALUES (?,?,?,?)',
        (fs.slug, fs.rank, fs.ts.isoformat(), json.dumps(fs.to_dict(), default=str)),
    )
