from __future__ import annotations

import importlib
import pkgutil

import wfm.analyzers
from wfm.config import Config

# Modules that make up the registry machinery itself, not analyzers.
_INFRASTRUCTURE = {"registry", "runner", "base"}

_REGISTERED: dict[str, object] = {}


def register(analyzer) -> None:
    _REGISTERED[analyzer.name] = analyzer


def discover() -> None:
    """Import every wfm/analyzers/*.py that exposes ANALYZER and register it.

    Drop-in: a new analyzer file needs no edit here. Names are visited in sorted
    order so registration is deterministic.
    """
    for info in sorted(pkgutil.iter_modules(wfm.analyzers.__path__), key=lambda i: i.name):
        if info.name in _INFRASTRUCTURE or info.name.startswith("_"):
            continue
        module = importlib.import_module(f"wfm.analyzers.{info.name}")
        analyzer = getattr(module, "ANALYZER", None)
        if analyzer is not None:
            register(analyzer)


discover()


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
