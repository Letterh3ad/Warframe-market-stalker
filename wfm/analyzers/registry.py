from __future__ import annotations

import importlib
import logging
import pkgutil

import wfm.analyzers
from wfm.config import Config

log = logging.getLogger(__name__)

# Modules that make up the registry machinery itself, not analyzers.
_INFRASTRUCTURE = {"registry", "runner", "base"}

# Analyzers that ship with the tool. A broken one is a bug, not a skippable scratch
# file, so discovery failing to load any of these is fatal.
_CORE = {"flip", "revert", "selltime", "set_arbitrage"}

_REGISTERED: dict[str, object] = {}


def register(analyzer) -> None:
    _REGISTERED[analyzer.name] = analyzer


def discover() -> None:
    """Import every wfm/analyzers/*.py that exposes ANALYZER and register it.

    Drop-in: a new analyzer file needs no edit here. Names are visited in sorted
    order so registration is deterministic. A non-core module that fails to import is
    logged and skipped, so a broken scratch file does not take down every command that
    pulls in the registry; a broken core analyzer still raises.
    """
    for info in sorted(pkgutil.iter_modules(wfm.analyzers.__path__), key=lambda i: i.name):
        if info.name in _INFRASTRUCTURE or info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"wfm.analyzers.{info.name}")
        except Exception:
            if info.name in _CORE:
                raise
            log.warning("skipping analyzer module %r: import failed", info.name, exc_info=True)
            continue
        analyzer = getattr(module, "ANALYZER", None)
        if analyzer is not None:
            register(analyzer)

    missing = _CORE - _REGISTERED.keys()
    if missing:
        raise RuntimeError(f"core analyzers failed to register: {sorted(missing)}")


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
