from __future__ import annotations

import statistics

from wfm.services import catalog_service
from wfm.services.context import AppContext

SUGGEST_WINDOW_DAYS = 30


def add(
    ctx: AppContext,
    query: str,
    rank: str | int | None = None,
    pin: float = 0.0,
    alert: bool = False,
) -> dict:
    slug, ranks = catalog_service.resolve(ctx, query, rank)
    for r in ranks:
        ctx.watchlist.add(slug, r, ctx.clock.utcnow(), pin_weight=pin, alert_override=alert)
    return {"slug": slug, "ranks": ranks, "pin_weight": pin, "alert_override": alert}


def remove(ctx: AppContext, query: str, rank: str | int | None = None) -> dict:
    slug, ranks = catalog_service.resolve(ctx, query, rank)
    removed = sum(1 for r in ranks if ctx.watchlist.remove(slug, r))
    return {"slug": slug, "removed": removed}


def list_(ctx: AppContext) -> list[dict]:
    out = []
    for entry in ctx.watchlist.all():
        item = ctx.items.get(entry.slug)
        out.append(
            {
                "slug": entry.slug,
                "rank": entry.rank,
                "name": item.name if item else entry.slug,
                "pin_weight": entry.pin_weight,
                "alert_override": entry.alert_override,
                "added_at": entry.added_at.isoformat(),
            }
        )
    return out


def suggest(ctx: AppContext, top: int = 20) -> list[dict]:
    """Ranks catalog items by tradeability. Never adds anything: the user confirms."""
    watched = {(e.slug, e.rank) for e in ctx.watchlist.all()}
    scored: list[dict] = []
    for slug in ctx.items.all_slugs():
        item = ctx.items.get(slug)
        rank = item.canonical_rank if item else 0
        if (slug, rank) in watched:
            continue
        candles = ctx.daily.window(slug, rank, days=SUGGEST_WINDOW_DAYS)
        closes = [c.close for c in candles if c.close is not None]
        volumes = [c.volume for c in candles if c.volume is not None]
        if len(closes) < 7 or not volumes:
            continue
        volume = statistics.median(volumes)
        spread = statistics.median(
            [c.high - c.low for c in candles if c.high is not None and c.low is not None] or [0]
        )
        volatility = statistics.pstdev(closes) / statistics.median(closes) if closes else 0.0
        scored.append(
            {
                "slug": slug,
                "rank": rank,
                "name": item.name if item else slug,
                "median_volume": volume,
                "volatility": round(volatility, 4),
                "median_daily_range": spread,
                "score": round(volume * volatility, 3),
            }
        )
    return sorted(scored, key=lambda s: s["score"], reverse=True)[:top]
