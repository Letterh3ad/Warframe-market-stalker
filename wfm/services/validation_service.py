from __future__ import annotations

from dataclasses import asdict

from wfm.analyzers import registry
from wfm.services.context import AppContext
from wfm.validation.harness import replay, sweep_thresholds


def validate(
    ctx: AppContext,
    start: str,
    end: str,
    analyzer: str | None = None,
    horizon_days: int = 7,
    sweep_key: str | None = None,
    sweep_values: list | None = None,
) -> list[dict]:
    names = [analyzer] if analyzer else [a.name for a in registry.enabled(ctx.config)]
    if sweep_key and sweep_values:
        rows: list[dict] = []
        for name in names:
            for value, result in sweep_thresholds(
                ctx, name, key=sweep_key, values=sweep_values,
                start=start, end=end, horizon_days=horizon_days,
            ):
                rows.append({sweep_key: value, **asdict(result)})
        return rows
    return [
        asdict(replay(ctx, name, start=start, end=end, horizon_days=horizon_days))
        for name in names
    ]
