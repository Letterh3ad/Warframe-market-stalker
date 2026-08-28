from __future__ import annotations

from wfm.clock import Clock
from wfm.config import MAX_REQUESTS_PER_SECOND


class TokenBucket:
    """Continuous refill, capacity one. No bursts by design.

    A burst is what trips a server side limiter even when the average rate is legal,
    so this paces every request rather than allowing saved-up credit to be spent.
    """

    def __init__(self, rate_per_second: float, clock: Clock) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if rate_per_second > MAX_REQUESTS_PER_SECOND:
            raise ValueError(
                f"rate_per_second {rate_per_second} exceeds the published ceiling "
                f"{MAX_REQUESTS_PER_SECOND}"
            )
        self._interval = 1.0 / rate_per_second
        self._rate = rate_per_second
        self._clock = clock
        self._next_allowed: float | None = None

    @property
    def rate_per_second(self) -> float:
        return self._rate

    async def acquire(self) -> None:
        now = self._clock.now()
        if self._next_allowed is None or now >= self._next_allowed:
            self._next_allowed = now + self._interval
            return
        wait = self._next_allowed - now
        await self._clock.sleep(wait)
        self._next_allowed = self._clock.now() + self._interval
