from __future__ import annotations

import sys
from typing import TextIO

from wfm.alerts.base import DeliveryResult
from wfm.alerts.format import render_signal
from wfm.models import Signal


class TerminalSink:
    """Always on, no configuration. Every signal lands here."""

    name = "terminal"

    def __init__(self, stream: TextIO | None = None, names: dict[str, str] | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._names = names or {}

    async def deliver(self, signals: list[Signal]) -> DeliveryResult:
        for signal in signals:
            self._stream.write(render_signal(signal, name=self._names.get(signal.slug)) + "\n")
        if signals:
            self._stream.flush()
        return DeliveryResult(
            sink=self.name, delivered=[s.id for s in signals if s.id is not None]
        )

    async def deliver_text(self, text: str) -> DeliveryResult:
        self._stream.write(text + "\n")
        self._stream.flush()
        return DeliveryResult(sink=self.name)
