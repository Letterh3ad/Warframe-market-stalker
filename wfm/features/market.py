from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date

from wfm.features import price
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
    # The anchor the peer returns were measured against. An item's own return must use
    # the same one, or a clock that rolls past midnight between building the context and
    # measuring the item makes the two sides of an excess return non-comparable.
    anchor: date | None = None


def returns_over(
    candles: list[DailyCandle], days: int, end: date | None = None
) -> float | None:
    """Change across the `days` calendar days ending at `end`, or None if uncovered.

    Anchored on dates rather than on the last n closes: the API omits untraded days, so an
    illiquid item's 8 newest closes can span months, and reporting that as a 7 day return
    invents a move that never happened. Starts from price.window's covered window, then
    adds a stricter rule of its own: the covered days must span exactly `days`, closes on
    both edges. price.window would emit a median for a window covered everywhere but the
    anchor day; a return measured over that window is not a `days` day return and would
    not be comparable to a peer's. Stricter here on purpose, so every per_item return in
    a cohort covers the identical calendar span.
    """
    # days + 1 points span days intervals
    inside = price.window(candles, days + 1, end)
    if inside is None:
        return None
    # The coverage gate tolerates a missing day, and if the tolerated day sits on an
    # edge the move spans fewer days than the label claims, which would make the two
    # sides of an excess return non-comparable.
    span = (
        date.fromisoformat(inside[-1].date) - date.fromisoformat(inside[0].date)
    ).days
    if span != days:
        return None
    opening = inside[0].close
    if not opening:
        return None
    return (inside[-1].close - opening) / opening


def build_context(
    series: dict[str, list[DailyCandle]],
    tags: dict[str, tuple[str, ...]],
    days: int = 7,
    end: date | None = None,
) -> MarketContext:
    per_item = {
        slug: value
        for slug, candles in series.items()
        if (value := returns_over(candles, days, end=end)) is not None
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
        anchor=end,
    )


def build(
    item_candles: list[DailyCandle],
    tags: tuple[str, ...],
    context: MarketContext,
    days: int = 7,
    slug: str | None = None,
    end: date | None = None,
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
    # The market fallback drops the item too. Excluding it from its cohort but leaving it
    # in the market median still benchmarks a lone item against itself, which is how a
    # meaningless excess of exactly 0.0 gets reported with confidence.
    market_peers = [value for s, value in context.per_item.items() if s != slug]
    market_fallback = statistics.median(market_peers) if market_peers else None
    # None, not the market median: a market-wide number under a tag-specific name reads
    # as a cohort comparison that never happened.
    benchmark = cohort_return if cohort_return is not None else market_fallback
    own = returns_over(item_candles, days, end=end)

    return (
        MarketFeatures(
            # The item-exclusive market median: this is the benchmark excess_return_7d
            # actually uses when the item has no cohort, so it must be the number reported.
            market_median_return_7d=market_fallback,
            tag=tag,
            tag_median_return_7d=cohort_return,
            excess_return_7d=(own - benchmark)
            if own is not None and benchmark is not None
            else None,
            cohort_size=len(peers),
        ),
        {"market_cohort": len(peers)},
    )
