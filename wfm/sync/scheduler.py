from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from datetime import timedelta

from wfm.clock import Clock
from wfm.config import Config
from wfm.models import WatchlistEntry
from wfm.store.poll_state import PollStateRepo


@dataclass(frozen=True)
class Weights:
    vol: float = 1.0
    liq: float = 0.5
    spread: float = 0.8
    pin: float = 1.5

    @classmethod
    def from_config(cls, config: Config) -> Weights:
        return cls(
            vol=config.w_vol, liq=config.w_liq, spread=config.w_spread, pin=config.w_pin
        )


@dataclass(frozen=True)
class ScoreInputs:
    volatility: float | None = None
    volume: float | None = None
    online_spread_pct: float | None = None
    pin_weight: float = 0.0


def score(inputs: ScoreInputs, weights: Weights) -> float:
    volatility = inputs.volatility or 0.0
    volume = inputs.volume or 0.0
    spread = inputs.online_spread_pct or 0.0
    return (
        weights.vol * volatility
        + weights.liq * math.log1p(volume) / 10.0
        + weights.spread * spread
        + weights.pin * inputs.pin_weight
    )


def interval_minutes(
    value: float, floor: int = 30, ceiling: int = 2, saturation: float = 1.0
) -> float:
    fraction = min(1.0, max(0.0, value / saturation)) if saturation > 0 else 0.0
    return floor - fraction * (floor - ceiling)


@dataclass
class QueueItem:
    slug: str
    rank: int
    pin_weight: float
    due_at: float
    interval_minutes: float
    unchanged_polls: int = 0

    def key(self) -> tuple[str, int]:
        return (self.slug, self.rank)


class PollQueue:
    """A heap on due_at. Holds no budget and issues no request.

    due_at lives in clock.now() monotonic seconds, which reset to zero on every
    process start. When state is given, rebuild() and reschedule() translate to
    and from the wall-clock due_at that PollStateRepo persists, so a restart
    resumes the real schedule instead of treating everything as due at once.
    """

    def __init__(
        self,
        clock: Clock,
        floor_minutes: int = 30,
        ceiling_minutes: int = 2,
        decay_after: int = 3,
        saturation: float = 1.0,
        state: PollStateRepo | None = None,
        catchup_max_items: int = 25,
    ) -> None:
        self._clock = clock
        self._floor = floor_minutes
        self._ceiling = ceiling_minutes
        self._decay_after = decay_after
        self._saturation = saturation
        self._state = state
        self._catchup_max_items = catchup_max_items
        self._heap: list[tuple[float, int, QueueItem]] = []
        self._items: dict[tuple[str, int], QueueItem] = {}
        self._counter = 0
        # Keys currently checked out via pop_due but not yet returned via reschedule.
        # A rebuild must not resurrect these with their old, already-passed due_at,
        # or the runner gets handed the same slug again seconds after the first poll.
        self._inflight: set[tuple[str, int]] = set()

    @property
    def size(self) -> int:
        return len(self._items)

    def rebuild(self, entries: list[WatchlistEntry]) -> None:
        wanted = {(e.slug, e.rank): e for e in entries}
        stored = self._state.all() if self._state is not None else {}

        for key in (set(self._items) | set(stored)) - set(wanted):
            self._items.pop(key, None)
            self._inflight.discard(key)
            self._state_delete(*key)

        now = self._clock.now()
        now_utc = self._clock.utcnow()
        overdue_restored: list[tuple] = []  # (stored wall due_at, item), oldest sorts first
        for key, entry in wanted.items():
            if key in self._items:
                self._items[key].pin_weight = entry.pin_weight
                continue
            row = stored.get(key)
            if row is None:
                self._items[key] = QueueItem(
                    slug=entry.slug, rank=entry.rank, pin_weight=entry.pin_weight,
                    due_at=now, interval_minutes=self._floor,
                )
                continue
            due_at = now + (row["due_at"] - now_utc).total_seconds()
            item = QueueItem(
                slug=entry.slug, rank=entry.rank, pin_weight=entry.pin_weight,
                due_at=due_at, interval_minutes=row["interval_minutes"],
                unchanged_polls=row["unchanged_polls"],
            )
            self._items[key] = item
            if due_at <= now:
                overdue_restored.append((row["due_at"], item))

        # Bounded catch-up: a sleep gap can leave hundreds of items overdue at once.
        # The budget would serialize that burst anyway, so releasing it all as "due
        # now" only makes the loop look wedged; keep the most-starved items due now
        # and spread the rest across the floor interval instead of firing together.
        overdue_restored.sort(key=lambda pair: pair[0])
        deferred = overdue_restored[self._catchup_max_items :]
        if deferred:
            span = self._floor * 60
            n = len(deferred)
            for i, (_, item) in enumerate(deferred, start=1):
                item.due_at = now + span * i / (n + 1)

        self._reheap()

    def peek(self) -> QueueItem | None:
        self._drop_stale()
        return self._heap[0][2] if self._heap else None

    def pop_due(self) -> QueueItem | None:
        self._drop_stale()
        if not self._heap or self._heap[0][0] > self._clock.now():
            return None
        item = heapq.heappop(self._heap)[2]
        self._inflight.add(item.key())
        return item

    def seconds_until_next(self) -> float | None:
        item = self.peek()
        if item is None:
            return None
        return max(0.0, item.due_at - self._clock.now())

    def reschedule(self, item: QueueItem, score_value: float, changed: bool) -> None:
        self._inflight.discard(item.key())
        if item.key() not in self._items:
            return
        item.unchanged_polls = 0 if changed else item.unchanged_polls + 1
        interval = interval_minutes(
            score_value, floor=self._floor, ceiling=self._ceiling, saturation=self._saturation
        )
        if item.unchanged_polls >= self._decay_after:
            decay_steps = item.unchanged_polls - self._decay_after + 1
            interval = min(self._floor, interval * (2**decay_steps))
        item.interval_minutes = interval
        item.due_at = self._clock.now() + interval * 60
        self._push(item)
        self._state_upsert(item)

    def _push(self, item: QueueItem) -> None:
        self._counter += 1
        heapq.heappush(self._heap, (item.due_at, self._counter, item))

    def _reheap(self) -> None:
        self._heap = []
        self._counter = 0
        for key, item in self._items.items():
            # An in-flight item has no valid due_at to schedule from; pushing its old,
            # already-passed one would hand the runner the same slug a second time.
            # reschedule() re-pushes it once the poll actually completes.
            if key in self._inflight:
                continue
            self._push(item)

    def _drop_stale(self) -> None:
        while self._heap and self._heap[0][2].key() not in self._items:
            heapq.heappop(self._heap)

    def _state_upsert(self, item: QueueItem) -> None:
        if self._state is None:
            return
        wall_due = self._clock.utcnow() + timedelta(seconds=item.due_at - self._clock.now())
        self._state.upsert(
            slug=item.slug,
            rank=item.rank,
            due_at=wall_due,
            interval_minutes=item.interval_minutes,
            unchanged_polls=item.unchanged_polls,
            last_polled_at=self._clock.utcnow(),
        )

    def _state_delete(self, slug: str, rank: int) -> None:
        if self._state is None:
            return
        self._state.delete(slug, rank)
