from __future__ import annotations

from wfm.alerts.format import render_digest
from wfm.models import Signal

DEFAULT_CAP = 15


def build(signals: list[Signal], names: dict[str, str], cap: int = DEFAULT_CAP) -> str:
    return render_digest(signals, names=names, cap=cap)
