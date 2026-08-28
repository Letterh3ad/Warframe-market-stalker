from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Horizon(str, Enum):
    URGENT = "urgent"
    DAILY = "daily"


class Scope(str, Enum):
    ITEM = "item"
    GROUP = "group"


class Direction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class Side(str, Enum):
    """The subset of Direction a ledger row can hold; HOLD is not a trade."""

    BUY = "buy"
    SELL = "sell"


class SweepStatus(str, Enum):
    RUNNING = "running"
    DONE = "done"
    HALTED = "halted"


@dataclass(frozen=True)
class Item:
    slug: str
    name: str
    url_name: str
    tags: tuple[str, ...] = ()
    max_rank: int = 0
    canonical_rank: int = 0
    ducats: int | None = None
    is_set: bool = False
    last_seen_version: str | None = None


@dataclass(frozen=True)
class DailyCandle:
    slug: str
    rank: int
    date: str
    volume: int | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    median: float | None = None
    avg_price: float | None = None
    wa_price: float | None = None
    moving_avg: float | None = None
    donch_top: float | None = None
    donch_bot: float | None = None


@dataclass(frozen=True)
class HourlyCandle:
    slug: str
    rank: int
    ts: datetime
    volume: int | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    median: float | None = None
    avg_price: float | None = None
    wa_price: float | None = None


@dataclass(frozen=True)
class BookSnapshot:
    slug: str
    rank: int
    ts: datetime
    best_bid: int | None = None
    best_ask: int | None = None
    online_best_bid: int | None = None
    online_best_ask: int | None = None
    bid_depth: tuple[int, ...] = ()
    ask_depth: tuple[int, ...] = ()
    bid_count: int = 0
    ask_count: int = 0
    online_bid_count: int = 0
    online_ask_count: int = 0
    stale_share: float | None = None

    @property
    def spread(self) -> int | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    @property
    def online_spread(self) -> int | None:
        if self.online_best_bid is None or self.online_best_ask is None:
            return None
        return self.online_best_ask - self.online_best_bid


@dataclass(frozen=True)
class WatchlistEntry:
    slug: str
    rank: int
    added_at: datetime
    pin_weight: float = 0.0
    alert_override: bool = False


@dataclass(frozen=True)
class Signal:
    slug: str
    rank: int
    analyzer: str
    ts: datetime
    direction: Direction
    magnitude: float
    confidence: float
    evidence: dict = field(default_factory=dict)
    horizon: Horizon = Horizon.DAILY
    expires_at: datetime | None = None
    alerted_at: datetime | None = None
    id: int | None = None


@dataclass(frozen=True)
class Trade:
    slug: str
    rank: int
    ts: datetime
    side: Side
    quantity: int
    platinum: int
    note: str | None = None
    id: int | None = None


@dataclass(frozen=True)
class Group:
    name: str
    created_at: datetime
    id: int | None = None
