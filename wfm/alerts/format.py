from __future__ import annotations

from wfm.models import Horizon, Signal

# Which evidence keys lead for each analyzer, so the most decision-relevant numbers
# are read first. Unlisted keys still render, after these.
EVIDENCE_ORDER: dict[str, tuple[str, ...]] = {
    "flip": ("fair_value", "online_best_ask", "margin_plat", "margin_pct", "median_volume_30d"),
    "revert": ("robust_z", "excess_return_7d", "median_90d", "volume_trend"),
    "selltime": ("recommendation", "percentile_90d", "reference_price", "unrealized_pnl"),
}


def _evidence_line(signal: Signal) -> str:
    preferred = EVIDENCE_ORDER.get(signal.analyzer, ())
    keys = [k for k in preferred if k in signal.evidence]
    keys += [k for k in signal.evidence if k not in keys]
    return "  " + "  ".join(f"{k}={signal.evidence[k]}" for k in keys)


def render_signal(signal: Signal, name: str | None = None, width: int = 80) -> str:
    label = name or signal.slug
    head = (
        f"[{signal.analyzer}] {signal.direction.value.upper()} {label} (rank {signal.rank})  "
        f"magnitude={signal.magnitude} confidence={signal.confidence}"
    )
    lines = [head, _evidence_line(signal)]
    if signal.horizon is Horizon.URGENT and signal.expires_at is not None:
        lines.append(f"  expires {signal.expires_at.isoformat()}")
    return "\n".join(lines)


def render_digest(
    signals: list[Signal], names: dict[str, str] | None = None, cap: int = 15
) -> str:
    """One renderer for the live sink and `wfm signals`, so a stored signal reads weeks
    later exactly as it did when it fired."""
    if not signals:
        return "Daily digest: no signals."
    names = names or {}
    ranked = sorted(signals, key=lambda s: s.magnitude, reverse=True)
    shown, hidden = ranked[:cap], ranked[cap:]

    by_analyzer: dict[str, list[Signal]] = {}
    for signal in shown:
        by_analyzer.setdefault(signal.analyzer, []).append(signal)

    lines = [f"Daily digest: {len(signals)} signals"]
    for analyzer, group in by_analyzer.items():
        lines.append(f"{analyzer} ({len(group)})")
        for signal in group:
            lines.append(render_signal(signal, name=names.get(signal.slug)))
    if hidden:
        lines.append(f"and {len(hidden)} more below the cap")
    return "\n".join(lines)
