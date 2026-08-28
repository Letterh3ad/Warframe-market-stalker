from __future__ import annotations

from wfm.api.errors import CircuitOpen
from wfm.clock import Clock


class CircuitBreaker:
    """Halts rather than retrying into a block.

    The published rules allow the maintainers to restrict an IP without notice, so a
    run of rate limit responses is treated as a stop condition, not a slow down.
    """

    def __init__(
        self, clock: Clock, cooldown_s: float = 900.0, max_429: int = 3, max_5xx: int = 5
    ) -> None:
        self._clock = clock
        self._cooldown_s = cooldown_s
        self._max_429 = max_429
        self._max_5xx = max_5xx
        self._max_403 = max_429
        self._run_429 = 0
        self._run_5xx = 0
        self._run_403 = 0
        self._tripped_at: float | None = None
        self._reason = ""

    @property
    def is_open(self) -> bool:
        if self._tripped_at is None:
            return False
        if self._clock.now() - self._tripped_at >= self._cooldown_s:
            self._reset()
            return False
        return True

    @property
    def reason(self) -> str:
        return self._reason

    def record_success(self) -> None:
        self._run_429 = 0
        self._run_5xx = 0
        self._run_403 = 0

    # Each run is counted independently. Zeroing the others meant a server alternating
    # between rate limiting and failing was never a run of anything, and got hammered.
    def record_429(self) -> None:
        self._run_429 += 1
        if self._run_429 >= self._max_429:
            self._trip(f"{self._run_429} consecutive 429 responses")

    def record_5xx(self) -> None:
        self._run_5xx += 1
        if self._run_5xx >= self._max_5xx:
            self._trip(f"{self._run_5xx} consecutive 5xx responses")

    def record_forbidden(self) -> None:
        """A run of 403s is what an IP restriction looks like from here."""
        self._run_403 += 1
        if self._run_403 >= self._max_403:
            self._trip(f"{self._run_403} consecutive 403 responses")

    def check(self) -> None:
        if self.is_open:
            raise CircuitOpen(self._reason)

    def _trip(self, reason: str) -> None:
        # Responses already in flight when the breaker opened must not keep pushing
        # the cooldown back, or it never ends.
        if self.is_open:
            return
        self._tripped_at = self._clock.now()
        self._reason = reason

    def _reset(self) -> None:
        self._tripped_at = None
        self._run_429 = 0
        self._run_5xx = 0
        self._run_403 = 0
        self._reason = ""
