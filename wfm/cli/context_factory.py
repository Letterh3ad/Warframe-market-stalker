from __future__ import annotations

from wfm.config import Config
from wfm.services.context import AppContext


def build(args) -> AppContext:
    return AppContext(Config.load(getattr(args, "config", None)))
