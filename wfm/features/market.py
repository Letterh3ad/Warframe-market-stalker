from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from wfm.features.price import MIN_COVERAGE
from wfm.features.types import MarketFeatures
from wfm.models import DailyCandle


@dataclass(frozen=True)
class MarketContext:
    median_return: float | None = None
    tag_returns: dict[str, float] = field(default_factory=dict)
    cohort_sizes: dict[str, int] = field(default_factory=dict)
    per_item: dict[str, float] = field(default_factory=dict)
    tag_members: dict[str, tuple[str, ...]] = field(default_factory=dict)
    window_days: int = 7


def returns_over(candles: list[DailyCandle], days: int) -> float | None:
    """Change across the last `days` calendar days, or None if the window is not covered.

    Anchored on dates rather than on the last n closes: the API omits untraded days, so an
    illiquid item's 8 newest closes can span months, and reporting that as a 7 day return
    invents a move that never happened.
    """
    dated = [c for c in candles if c.close is not None]
    if not dated:
        return None
    end = date.fromisoformat(dated[-1].date)
    start = end - timedelta(days=days)
    inside = [c for c in dated if start <= date.fromisoformat(c.date) <= end]
    # days + 1 points span days intervals
    if len(inside) < math.ceil((days + 1) * MIN_COVERAGE):
        return None
    opening = inside[0].close
    if not opening:
        return None
    return (inside[-1].close - opening) / opening


def build_context(
    series: dict[str, list[DailyCandle]],
    tags: dict[str, tuple[str, ...]],
    days: int = 7,
) -> MarketContext:
    per_item = {
        slug: value
        for slug, candles in series.items()
        if (value := returns_over(candles, days)) is not None
    }
    members: dict[str, list[str]] = {}
    for slug in per_item:
        primary = tags.get(slug, ())
        if primary:
            members.setdefault(primary[0], []).append(slug)
    return MarketContext(
        median_return=statistics.median(per_item.values()) if per_item else None,
        tag_returns={
            tag: statistics.median([per_item[s] for s in slugs]) for tag, slugs in members.items()
        },
        cohort_sizes={tag: len(slugs) for tag, slugs in members.items()},
        per_item=per_item,
        tag_members={tag: tuple(slugs) for tag, slugs in members.items()},
        window_days=days,
    )


def build(
    item_candles: list[DailyCandle],
    tags: tuple[str, ...],
    context: MarketContext,
    days: int = 7,
    slug: str | None = None,
) -> tuple[MarketFeatures, dict[str, int]]:
    """Separates an item dropping from everything dropping, which is the guard that stops
    a plat-wide slide reading as a buy signal on the whole catalog.

    The item is removed from its own cohort. Left in, an item that is the only sampled
    member of its tag is benchmarked against itself and reports a confident excess of
    exactly zero.
    """
    tag = tags[0] if tags else None
    peers = [
        context.per_item[s]
        for s in context.tag_members.get(tag, ())
        if s != slug and s in context.per_item
    ]
    cohort_return = statistics.median(peers) if peers else None
    # None, not the market median: a market-wide number under a tag-specific name reads
    # as a cohort comparison that never happened.
    benchmark = cohort_return if cohort_return is not None else context.median_return
    own = returns_over(item_candles, days)

    return (
        MarketFeatures(
            market_median_return_7d=context.median_return,
            tag=tag,
            tag_median_return_7d=cohort_return,
            excess_return_7d=(own - benchmark)
            if own is not None and benchmark is not None
            else None,
            cohort_size=len(peers),
        ),
        {"market_cohort": len(peers)},
    )
