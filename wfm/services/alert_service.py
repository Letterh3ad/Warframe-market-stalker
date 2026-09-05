from __future__ import annotations

from datetime import datetime

from wfm.alerts import digest as digest_module
from wfm.alerts.base import DeliveryResult
from wfm.alerts.discord import DiscordSink
from wfm.alerts.format import render_signal
from wfm.alerts.routing import route, route_operational
from wfm.alerts.terminal import TerminalSink
from wfm.models import Horizon, Signal
from wfm.services.context import AppContext


def _names(ctx: AppContext, signals: list[Signal]) -> dict[str, str]:
    out: dict[str, str] = {}
    # A batch of signals routinely repeats slugs (multiple analyzers, multiple
    # horizons), so dedupe before the lookups rather than issuing one per signal.
    for slug in dict.fromkeys(signal.slug for signal in signals):
        item = ctx.items.get(slug)
        if item is not None:
            out[slug] = item.name
    return out


def build_sinks(ctx: AppContext, signals: list[Signal] | None = None) -> dict:
    names = _names(ctx, signals or [])
    sinks: dict = {"terminal": TerminalSink(names=names)}
    if ctx.config.discord_webhook_url:
        sinks["discord"] = DiscordSink(ctx.config.discord_webhook_url, names=names)
    return sinks


async def _close(sinks: dict) -> None:
    for sink in sinks.values():
        aclose = getattr(sink, "aclose", None)
        if aclose is not None:
            await aclose()


async def deliver(
    ctx: AppContext, signals: list[Signal], sinks: dict | None = None
) -> list[DeliveryResult]:
    """Live delivery for URGENT signals (and DAILY signals a per-item override forces
    live). Plain DAILY signals are left untouched for run_digest, the only path that
    batches them to Discord."""
    if not signals:
        return []
    built = sinks is None
    sinks = sinks if sinks is not None else build_sinks(ctx, signals)
    try:
        discord_configured = "discord" in sinks
        per_sink: dict[str, list[Signal]] = {name: [] for name in sinks}
        for signal in signals:
            entry = ctx.watchlist.get(signal.slug, signal.rank)
            override = bool(entry and entry.alert_override)
            if signal.horizon is not Horizon.URGENT and not override:
                continue
            for name in route(
                signal,
                discord_configured=discord_configured,
                min_confidence=ctx.config.discord_min_confidence,
                min_magnitude=ctx.config.discord_min_magnitude,
                alert_override=override,
            ):
                if name in per_sink:
                    per_sink[name].append(signal)

        results: list[DeliveryResult] = []
        for name, batch in per_sink.items():
            result = await sinks[name].deliver(batch)
            results.append(result)
            # Terminal is the sink of record: a signal is delivered once it has
            # printed, even if the optional Discord mirror later fails.
            if name == "terminal" and result.delivered:
                ctx.signals.mark_alerted(result.delivered, when=ctx.clock.utcnow())
        return results
    finally:
        if built:
            await _close(sinks)


async def run_digest(ctx: AppContext, sinks: dict | None = None) -> dict:
    pending = ctx.signals.undelivered(Horizon.DAILY)
    if not pending:
        return {"delivered": 0, "sinks": []}

    built = sinks is None
    sinks = sinks if sinks is not None else build_sinks(ctx, pending)
    try:
        # No cap: the digest renders every pending signal, because it is about to mark
        # all of them delivered and undelivered() will never surface them again.
        text = digest_module.build(pending, names=_names(ctx, pending), cap=len(pending))
        used: list[str] = []
        errors: list[str] = []
        terminal_ok = False
        for name, sink in sinks.items():
            result = (
                await sink.deliver_text(text)
                if hasattr(sink, "deliver_text")
                else await sink.deliver(pending)
            )
            used.append(name)
            if result.error:
                errors.append(f"{name}: {result.error}")
            elif name == "terminal":
                terminal_ok = True

        # Mark once the sink of record has the batch. A broken Discord webhook is
        # reported but does not make the terminal reprint a growing digest forever.
        # When no terminal sink is in the set (tests only), fall back to all-succeeded.
        delivered_ok = terminal_ok if "terminal" in sinks else not errors
        if not delivered_ok:
            return {"delivered": 0, "sinks": used, "error": "; ".join(errors)}
        marked = ctx.signals.mark_alerted(
            [s.id for s in pending if s.id is not None], when=ctx.clock.utcnow()
        )
        out = {"delivered": marked, "sinks": used}
        if errors:
            out["error"] = "; ".join(errors)
        return out
    finally:
        if built:
            await _close(sinks)


async def operational(
    ctx: AppContext, message: str, sinks: dict | None = None
) -> list[DeliveryResult]:
    built = sinks is None
    sinks = sinks if sinks is not None else build_sinks(ctx)
    try:
        targets = route_operational(discord_configured="discord" in sinks)
        results = []
        for name in targets:
            sink = sinks.get(name)
            if sink is None:
                continue
            results.append(
                await sink.deliver_text(message)
                if hasattr(sink, "deliver_text")
                else await sink.deliver([])
            )
        return results
    finally:
        if built:
            await _close(sinks)


def list_signals(
    ctx: AppContext,
    since: datetime | None = None,
    analyzer: str | None = None,
    slug: str | None = None,
    limit: int = 50,
) -> list[dict]:
    signals = ctx.signals.query(since=since, analyzer=analyzer, slug=slug, limit=limit)
    return [
        {
            "id": s.id, "slug": s.slug, "rank": s.rank, "analyzer": s.analyzer,
            "ts": s.ts.isoformat(), "horizon": s.horizon.value, "direction": s.direction.value,
            "magnitude": s.magnitude, "confidence": s.confidence, "evidence": s.evidence,
            "alerted_at": s.alerted_at.isoformat() if s.alerted_at else None,
        }
        for s in signals
    ]


def render_signals(ctx: AppContext, **query) -> str:
    signals = ctx.signals.query(**query)
    names = _names(ctx, signals)
    return "\n".join(render_signal(s, name=names.get(s.slug)) for s in signals)
