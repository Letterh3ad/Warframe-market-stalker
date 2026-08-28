from __future__ import annotations

import asyncio
import heapq
import itertools
from enum import IntEnum

from wfm.api.ratelimit import TokenBucket
from wfm.clock import Clock


class Priority(IntEnum):
    INTERACTIVE = 0
    BACKGROUND = 1
    BULK = 2


class Budget:
    """Orders requests across one shared bucket.

    Two classes drawing on two buckets would make the real aggregate rate their sum,
    which is how a compliant looking client exceeds a limit, so there is exactly one.
    """

    def __init__(
        self, bucket: TokenBucket, clock: Clock, interactive_per_minute: int = 30
    ) -> None:
        self._bucket = bucket
        self._clock = clock
        self._interactive_per_minute = interactive_per_minute
        self._interactive_grants: list[float] = []
        self._spent = dict.fromkeys(Priority, 0)
        self._reservations: dict[str, int] = {}
        self._waiters: list[tuple[int, int, asyncio.Future]] = []
        self._sequence = itertools.count()
        self._busy = False

    async def acquire(self, priority: Priority = Priority.BACKGROUND) -> None:
        effective = self._effective_priority(priority)
        await self._enter(effective)
        try:
            await self._bucket.acquire()
        finally:
            self._leave()
        self._spent[priority] += 1
        if priority is Priority.INTERACTIVE:
            self._interactive_grants.append(self._clock.now())

    def interactive_remaining(self) -> int:
        self._expire_interactive_window()
        return max(0, self._interactive_per_minute - len(self._interactive_grants))

    def reserve(self, name: str, count: int) -> None:
        self._reservations[name] = count

    def release_reservation(self, name: str) -> None:
        self._reservations.pop(name, None)

    def spent(self, priority: Priority) -> int:
        return self._spent[priority]

    @property
    def total_spent(self) -> int:
        return sum(self._spent.values())

    def remaining_for(self, priority: Priority, horizon_s: float) -> int:
        allowance = int(self._bucket.rate_per_second * horizon_s)
        # BULK ignores reservations because the sweep is the holder of the reservation,
        # and subtracting its own reservation from its own allowance would halve it.
        if priority is Priority.BULK:
            return max(0, allowance)
        return max(0, allowance - sum(self._reservations.values()))

    def _effective_priority(self, priority: Priority) -> Priority:
        # Demoted, not rejected: the cap exists so a frontend cannot starve the poll
        # loop. It is not a second rate limit.
        if priority is Priority.INTERACTIVE and self.interactive_remaining() == 0:
            return Priority.BACKGROUND
        return priority

    def _expire_interactive_window(self) -> None:
        cutoff = self._clock.now() - 60.0
        self._interactive_grants = [t for t in self._interactive_grants if t > cutoff]

    async def _enter(self, priority: Priority) -> None:
        if not self._busy and not self._waiters:
            self._busy = True
            return
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        entry = (int(priority), next(self._sequence), future)
        heapq.heappush(self._waiters, entry)
        try:
            await future
        except asyncio.CancelledError:
            # A cancelled waiter that stayed in the heap would be handed the slot by
            # _leave() and drop it, stranding every waiter behind it.
            if entry in self._waiters:
                self._waiters.remove(entry)
                heapq.heapify(self._waiters)
            elif future.done() and not future.cancelled():
                self._leave()
            raise

    def _leave(self) -> None:
        while self._waiters:
            _, _, future = heapq.heappop(self._waiters)
            if future.cancelled():
                continue
            future.set_result(None)
            return
        self._busy = False
