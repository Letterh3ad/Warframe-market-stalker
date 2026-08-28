from __future__ import annotations

from datetime import datetime, timedelta


class FakeClock:
    def __init__(self, start_utc: datetime, start_monotonic: float = 0.0) -> None:
        self._monotonic = start_monotonic
        self._utc = start_utc
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self._monotonic

    def utcnow(self) -> datetime:
        return self._utc

    def advance(self, seconds: float) -> None:
        self._monotonic += seconds
        self._utc += timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)
