from __future__ import annotations

import json
from dataclasses import asdict

from wfm.api.endpoints import fetch_orders
from wfm.features import book as book_features
from wfm.features.market import MarketContext
from wfm.models import BookSnapshot
from wfm.services import catalog_service, feature_service
from wfm.services.context import AppContext
from wfm.sync.budget import Priority


async def poll_book(
    ctx: AppContext, slug: str, rank: int, priority: Priority = Priority.BACKGROUND
) -> BookSnapshot:
    client = ctx.new_client()
    orders = await fetch_orders(client, slug, priority=priority)
    now = ctx.clock.utcnow()
    snapshot = book_features.summarize(orders, slug=slug, rank=rank, ts=now)
    ctx.orders.insert(snapshot)
    ctx.raw_orders.maybe_store(
        slug,
        rank,
        now,
        json.dumps([asdict(o) for o in orders], default=str),
        sample_rate=ctx.config.raw_sample_rate,
    )
    return snapshot


async def report(
    ctx: AppContext,
    slug_query: str,
    rank: str | int | None = None,
    refresh: bool = False,
    market: MarketContext | None = None,
) -> dict:
    slug, ranks = catalog_service.resolve(ctx, slug_query, rank)
    if len(ranks) > 1:
        raise ValueError(
            f"{slug_query!r} resolves to {len(ranks)} ranks. report covers one rank at a "
            "time, so give --rank <n>."
        )
    target_rank = ranks[0]
    now = ctx.clock.utcnow()

    if refresh:
        snapshot = await poll_book(ctx, slug, target_rank, priority=Priority.INTERACTIVE)
    else:
        snapshot = ctx.orders.latest(slug, target_rank)

    if market is None:
        market = feature_service.market_context(ctx, now=now)
    fs = feature_service.build_for(
        ctx, slug, target_rank, snapshot=snapshot, market=market, now=now
    )
    feature_service.persist(ctx, fs)

    payload = fs.to_dict()
    item = ctx.items.get(slug)
    payload["name"] = item.name if item else slug
    payload["book_age_seconds"] = (
        int((now - snapshot.ts).total_seconds()) if snapshot is not None else None
    )
    payload["watched"] = ctx.watchlist.get(slug, target_rank) is not None
    return payload


MAX_HISTORY_DAYS = 365


def history(
    ctx: AppContext,
    slug_query: str,
    rank: str | int | None = None,
    days: int = 90,
) -> list[dict]:
    days = max(1, min(int(days), MAX_HISTORY_DAYS))
    slug, ranks = catalog_service.resolve(ctx, slug_query, rank)
    if len(ranks) > 1:
        raise ValueError(
            f"{slug_query!r} resolves to {len(ranks)} ranks. history covers one rank at "
            "a time, so give a rank."
        )
    # end= the newest complete day, not today: these candles come from the API's
    # statistics_closed and never include the current day, so anchoring on today
    # silently drops one day off the requested window.
    anchor = feature_service.anchor_date(ctx, ctx.clock.utcnow())
    return [
        {
            "date": c.date, "open": c.open, "high": c.high,
            "low": c.low, "close": c.close, "volume": c.volume,
        }
        for c in ctx.daily.window(slug, ranks[0], days, end=anchor)
    ]


async def report_group(ctx: AppContext, name: str, refresh: bool = False) -> dict:
    # Built once for the whole group: it is a market-wide figure that moves slowly, and
    # rebuilding it per member costs a full sampled catalog pass each time.
    market = feature_service.market_context(ctx, now=ctx.clock.utcnow())
    members = ctx.groups.members(name)
    return {
        "name": name,
        "items": [
            await report(ctx, slug, rank=rank, refresh=refresh, market=market)
            for slug, rank in members
        ],
    }
