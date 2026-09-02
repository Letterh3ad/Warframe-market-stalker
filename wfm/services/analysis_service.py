from __future__ import annotations

from datetime import datetime, timedelta

from wfm.analyzers import registry
from wfm.analyzers.base import Context, Holding
from wfm.analyzers.runner import run_group, run_item
from wfm.features.market import MarketContext
from wfm.models import BookSnapshot, Signal
from wfm.services import feature_service
from wfm.services.context import AppContext


def build_context(ctx: AppContext, now: datetime | None = None) -> Context:
    holdings = {
        (slug, rank): Holding(slug, rank, quantity, avg_cost)
        for slug, rank, quantity, avg_cost in ctx.trades.holdings()
    }
    watchlist = {
        (entry.slug, entry.rank): {
            "pin_weight": entry.pin_weight,
            "alert_override": entry.alert_override,
        }
        for entry in ctx.watchlist.all()
    }
    return Context(
        now=now or ctx.clock.utcnow(),
        holdings=holdings,
        watchlist=watchlist,
        thresholds=registry.thresholds(ctx.config),
    )


def _is_duplicate(ctx: AppContext, signal: Signal, now: datetime) -> bool:
    # Both instants are tz-aware: ctx.clock.utcnow() is aware, and last_signal_at parses
    # a stored "+00:00" ISO string back to an aware datetime.
    open_signals = ctx.signals.open_for(signal.slug, signal.rank, signal.analyzer, now)
    # A reversed direction is a new opportunity, not the same one: a still-open HOLD must
    # not swallow the day's later SELL, nor an open BUY the revert flip back to SELL.
    if any(s.direction is signal.direction for s in open_signals):
        return True
    last = ctx.signals.last_signal_at(signal.slug, signal.rank, signal.analyzer)
    cooldown = timedelta(minutes=ctx.config.cooldown_minutes)
    # The cooldown is intentionally direction-blind: it is noise control, not opportunity
    # tracking, so a rapid flip back the other way is still throttled.
    return last is not None and now - last < cooldown


def analyze_item(
    ctx: AppContext,
    slug: str,
    rank: int,
    snapshot: BookSnapshot | None = None,
    market: MarketContext | None = None,
    now: datetime | None = None,
    persist: bool = True,
) -> dict:
    now = now or ctx.clock.utcnow()
    market = market if market is not None else feature_service.market_context(ctx, now=now)
    fs = feature_service.build_for(ctx, slug, rank, snapshot=snapshot, market=market, now=now)
    feature_service.persist(ctx, fs)

    analyzer_ctx = build_context(ctx, now=now)
    signals, skipped = run_item(registry.enabled(ctx.config), fs, analyzer_ctx)

    kept: list[Signal] = []
    suppressed: list[str] = []
    for signal in signals:
        if _is_duplicate(ctx, signal, now):
            suppressed.append(signal.analyzer)
            continue
        if persist:
            ctx.signals.insert(signal)
        kept.append(signal)

    return {
        "slug": slug,
        "rank": rank,
        "signals": [_as_dict(s) for s in kept],
        "skipped": skipped,
        "suppressed": suppressed,
    }


def analyze_group(ctx: AppContext, name: str, persist: bool = True) -> dict:
    now = ctx.clock.utcnow()
    market = feature_service.market_context(ctx, now=now)
    members = ctx.groups.members(name)
    per_item = [
        analyze_item(ctx, slug, rank, market=market, now=now, persist=persist)
        for slug, rank in members
    ]
    feature_sets = [
        feature_service.build_for(ctx, slug, rank, market=market, now=now)
        for slug, rank in members
    ]
    group_signals, skipped = run_group(
        registry.enabled(ctx.config), feature_sets, build_context(ctx, now=now)
    )
    if persist:
        for signal in group_signals:
            ctx.signals.insert(signal)
    return {
        "name": name,
        "items": per_item,
        "group_signals": [_as_dict(s) for s in group_signals],
        "skipped": skipped,
    }


def _as_dict(signal: Signal) -> dict:
    return {
        "slug": signal.slug,
        "rank": signal.rank,
        "analyzer": signal.analyzer,
        "ts": signal.ts.isoformat(),
        "horizon": signal.horizon.value,
        "direction": signal.direction.value,
        "magnitude": signal.magnitude,
        "confidence": signal.confidence,
        "evidence": signal.evidence,
        "expires_at": signal.expires_at.isoformat() if signal.expires_at else None,
    }
