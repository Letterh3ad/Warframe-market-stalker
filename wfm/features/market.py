from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from wfm.features.types import MarketFeatures
from wfm.models import DailyCandle


@dataclass(frozen=True)
class MarketContext:
    median_return: float | None = None
    tag_returns: dict[str, float] = field(default_factory=dict)
    cohort_sizes: dict[str, int] = field(default_factory=dict)
    window_days: int = 7


def returns_over(candles: list[DailyCandle], days: int) -> float | None:
    closes = [c.close for c in candles if c.close is not None]
    if len(closes) <= days:
        return None
    start, end = closes[-(days + 1)], closes[-1]
    if not start:
        return None
    return (end - start) / start


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
    by_tag: dict[str, list[float]] = {}
    for slug, value in per_item.items():
        primary = tags.get(slug, ())
        if primary:
            by_tag.setdefault(primary[0], []).append(value)
    return MarketContext(
        median_return=statistics.median(per_item.values()) if per_item else None,
        tag_returns={tag: statistics.median(values) for tag, values in by_tag.items()},
        cohort_sizes={tag: len(values) for tag, values in by_tag.items()},
        window_days=days,
    )


def build(
    item_candles: list[DailyCandle],
    tags: tuple[str, ...],
    context: MarketContext,
    days: int = 7,
) -> tuple[MarketFeatures, dict[str, int]]:
    """Separates an item dropping from everything dropping, which is the guard that stops
    a plat-wide slide reading as a buy signal on the whole catalog.
    """
    tag = tags[0] if tags else None
    cohort_return = context.tag_returns.get(tag) if tag else None
    cohort_size = context.cohort_sizes.get(tag, 0) if tag else 0
    benchmark = cohort_return if cohort_return is not None else context.median_return
    own = returns_over(item_candles, days)

    return (
        MarketFeatures(
            market_median_return_7d=context.median_return,
            tag=tag,
            tag_median_return_7d=benchmark,
            excess_return_7d=(own - benchmark)
            if own is not None and benchmark is not None
            else None,
            cohort_size=cohort_size,
        ),
        {"market_cohort": cohort_size},
    )
