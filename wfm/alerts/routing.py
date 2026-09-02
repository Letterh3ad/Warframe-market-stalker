from __future__ import annotations

from wfm.models import Horizon, Signal


def route(
    signal: Signal,
    discord_configured: bool,
    min_confidence: float,
    min_magnitude: float,
    alert_override: bool = False,
) -> set[str]:
    """Pure signal -> sink-name mapping. Terminal always. Discord only for URGENT
    signals past both thresholds, or anything with an explicit per-item override.
    DAILY signals reach Discord through the digest, never live: a mean-reversion
    entry is as good tomorrow and sending it twice is noise."""
    sinks = {"terminal"}
    if not discord_configured:
        return sinks
    if alert_override:
        sinks.add("discord")
        return sinks
    if signal.horizon is Horizon.URGENT and (
        signal.confidence >= min_confidence and signal.magnitude >= min_magnitude
    ):
        sinks.add("discord")
    return sinks


def route_operational(discord_configured: bool) -> set[str]:
    return {"terminal", "discord"} if discord_configured else {"terminal"}
