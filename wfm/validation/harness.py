"""Signal replay harness.

Scope limits, deliberate and not yet lifted:

- ITEM-scoped analyzers only. `replay` raises on a GROUP analyzer.
- Rank-0 daily series only. The target series, the synthetic holding and the
  `FeatureSet` are all built at rank 0, whereas production
  `feature_service.market_context` uses `item.canonical_rank`. For a ranked mod whose
  canonical rank is non-zero, production evaluates a different price series, so a
  replayed hit rate for such an item does not transfer directly. Phases 6-7 re-tune
  against accrued live data.
- Forward-return scoring loads `horizon_days` of candles past `end` for the target
  series so a signal emitted near `end` still has a candle to score against. Those
  extra candles reach an analyzer only through `_forward_return`; the per-day
  `c.date <= as_of` slice keeps them out of every feature window.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from wfm.analyzers import registry
from wfm.analyzers.base import Context, Holding
from wfm.features import market as market_features
from wfm.features import price as price_features
from wfm.features.types import FeatureSet, Provenance
from wfm.models import Direction, Scope
from wfm.services.context import AppContext
from wfm.services.feature_service import spread

PRICE_WINDOW_DAYS = 90
MARKET_SAMPLE_LIMIT = 500
_LOAD_DAYS = 400
# How far past the horizon date a candle may sit and still count as the forward
# return. Beyond this the series is too sparse to attribute an N-day move, so the
# signal is left unscored rather than credited a much longer swing.
_FORWARD_SLACK_DAYS = 3


@dataclass(frozen=True)
class ReplayResult:
    analyzer: str
    signals: int = 0
    hits: int = 0
    hit_rate: float | None = None
    median_forward_return: float | None = None
    by_direction: dict = field(default_factory=dict)


def _forward_return(candles: list, as_of: str, horizon_days: int) -> float | None:
    by_date = {c.date: c.close for c in candles if c.close is not None}
    start = by_date.get(as_of)
    if not start:
        return None
    target = date.fromisoformat(as_of) + timedelta(days=horizon_days)
    cutoff = (target + timedelta(days=_FORWARD_SLACK_DAYS)).isoformat()
    later = [d for d in sorted(by_date) if d >= target.isoformat()]
    if not later or later[0] > cutoff:
        return None
    return (by_date[later[0]] - start) / start


def _feature_set_as_of(slug, window, tags, market_context, as_of: str) -> FeatureSet:
    anchor = date.fromisoformat(as_of)
    price_block, samples = price_features.build(window, end=anchor)
    market_block, market_samples = market_features.build(
        window, tags=tags, context=market_context, slug=slug, end=anchor
    )
    samples.update(market_samples)
    return FeatureSet(
        slug=slug,
        rank=0,
        ts=datetime.fromisoformat(as_of).replace(tzinfo=timezone.utc),
        price=price_block,
        market=market_block,
        provenance=Provenance(samples=samples, available=frozenset({"price", "market"})),
    )


def replay(
    ctx: AppContext,
    analyzer_name: str,
    start: str,
    end: str,
    horizon_days: int = 7,
    slugs: list[str] | None = None,
    sample: int | None = None,
    threshold_overrides: dict | None = None,
) -> ReplayResult:
    analyzer = registry.get(analyzer_name)
    if analyzer.scope is not Scope.ITEM:
        raise ValueError(
            f"{analyzer_name!r} is not an ITEM-scoped analyzer; replay only supports ITEM scope"
        )
    base = registry.thresholds(ctx.config)
    thresholds = {
        **base,
        analyzer_name: {**base.get(analyzer_name, {}), **(threshold_overrides or {})},
    }

    all_slugs = ctx.items.all_slugs()
    targets = slugs if slugs is not None else all_slugs
    if sample is not None:
        targets = spread(list(targets), sample)

    # Loaded once, sliced per day. The full catalog is far larger than the target set,
    # so the market context is built from a strided sample of it, matching production.
    sample_slugs = spread(all_slugs, MARKET_SAMPLE_LIMIT)
    def _load(slug_list, load_end=end, load_days=_LOAD_DAYS):
        out = {}
        for slug in slug_list:
            candles = ctx.daily.window(slug, 0, days=load_days, end=load_end)
            if candles:
                out[slug] = candles
        return out

    # The target series feeds both per-day feature slicing and forward-return scoring.
    # Load horizon_days past `end` so a signal emitted near `end` still has a candle to
    # score against; the extra future candles are reachable only via _forward_return,
    # never through the c.date <= as_of window slice below. The replay loop still stops
    # at `end`.
    target_end = (date.fromisoformat(end) + timedelta(days=horizon_days)).isoformat()
    target_series = _load(targets, load_end=target_end, load_days=_LOAD_DAYS + horizon_days)
    sample_series = _load(sample_slugs)
    tags = {slug: (item.tags if (item := ctx.items.get(slug)) else ()) for slug in all_slugs}

    returns: list[float] = []
    hits = 0
    by_direction: dict[str, dict] = {}

    current = date.fromisoformat(start)
    last = date.fromisoformat(end)
    while current <= last:
        as_of = current.isoformat()
        anchor = current
        sample_window = {
            slug: [c for c in candles if c.date <= as_of]
            for slug, candles in sample_series.items()
        }
        market_context = market_features.build_context(
            sample_window, tags, days=7, end=anchor
        )
        for slug, candles in target_series.items():
            window = [c for c in candles if c.date <= as_of][-PRICE_WINDOW_DAYS:]
            if not window or window[-1].date != as_of:
                continue
            fs = _feature_set_as_of(slug, window, tags.get(slug, ()), market_context, as_of)
            analyzer_ctx = Context(
                now=fs.ts,
                holdings={
                    (slug, 0): Holding(slug, 0, quantity=1, avg_cost=window[-1].close or 0)
                },
                thresholds=thresholds,
            )
            for signal in analyzer.evaluate(fs, analyzer_ctx):
                if signal.direction is Direction.HOLD:
                    continue
                forward = _forward_return(target_series[slug], as_of, horizon_days)
                if forward is None:
                    continue
                returns.append(forward)
                hit = forward > 0 if signal.direction is Direction.BUY else forward < 0
                hits += int(hit)
                bucket = by_direction.setdefault(
                    signal.direction.value, {"signals": 0, "hits": 0}
                )
                bucket["signals"] += 1
                bucket["hits"] += int(hit)
        current += timedelta(days=1)

    total = len(returns)
    return ReplayResult(
        analyzer=analyzer_name,
        signals=total,
        hits=hits,
        hit_rate=(hits / total) if total else None,
        median_forward_return=statistics.median(returns) if returns else None,
        by_direction=by_direction,
    )


def sweep_thresholds(
    ctx: AppContext, analyzer_name: str, key: str, values: list, **replay_kwargs
) -> list[tuple[object, ReplayResult]]:
    return [
        (value, replay(ctx, analyzer_name, threshold_overrides={key: value}, **replay_kwargs))
        for value in values
    ]
