from __future__ import annotations

from wfm.analyzers import flip, revert, selltime
from wfm.config import Config

_REGISTERED: dict[str, object] = {}


def register(analyzer) -> None:
    _REGISTERED[analyzer.name] = analyzer


for _module in (flip, revert, selltime):
    register(_module.ANALYZER)


def get(name: str):
    if name not in _REGISTERED:
        raise KeyError(f"no analyzer named {name!r}")
    return _REGISTERED[name]


def all() -> list:
    return list(_REGISTERED.values())


def enabled(config: Config) -> list:
    return [
        analyzer
        for analyzer in all()
        if config.analyzers.get(analyzer.name, {}).get("enabled", True)
    ]


def thresholds(config: Config) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for analyzer in all():
        overrides = {
            key: value
            for key, value in config.analyzers.get(analyzer.name, {}).items()
            if key != "enabled"
        }
        merged[analyzer.name] = {**analyzer.DEFAULTS, **overrides}
    return merged
