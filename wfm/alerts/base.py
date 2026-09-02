from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from wfm.models import Signal


@dataclass(frozen=True)
class DeliveryResult:
    sink: str
    delivered: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    error: str | None = None


class AlertSink(Protocol):
    name: str

    async def deliver(self, signals: list[Signal]) -> DeliveryResult: ...

    async def deliver_text(self, text: str) -> DeliveryResult: ...
