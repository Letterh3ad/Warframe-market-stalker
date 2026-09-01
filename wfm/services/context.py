from __future__ import annotations

import sqlite3

from wfm.api.breaker import CircuitBreaker
from wfm.api.client import WFMClient
from wfm.api.ratelimit import TokenBucket
from wfm.clock import Clock, SystemClock
from wfm.config import Config
from wfm.store.db import connect
from wfm.store.groups import GroupsRepo
from wfm.store.http_cache import HttpCacheRepo
from wfm.store.items import ItemsRepo
from wfm.store.migrate import migrate
from wfm.store.orders import OrderSnapshotsRepo, RawSnapshotsRepo
from wfm.store.signals import SignalsRepo
from wfm.store.stats import DailyStatsRepo, HourlyStatsRepo
from wfm.store.sweep import SweepStateRepo
from wfm.store.trades import TradesRepo
from wfm.store.watchlist import WatchlistRepo
from wfm.sync.budget import Budget


class AppContext:
    """Single place that owns the database connection and the one client stack.

    Services take a context rather than building their own, so a test can hand them a
    fake clock and an in-memory database without any service knowing it happened.
    """

    def __init__(
        self,
        config: Config,
        conn: sqlite3.Connection | None = None,
        clock: Clock | None = None,
        client: WFMClient | None = None,
    ) -> None:
        self.config = config
        self.clock = clock or SystemClock()
        self._owns_conn = conn is None
        self.conn = conn if conn is not None else connect(config.db_path)
        migrate(self.conn)
        self.breaker = CircuitBreaker(clock=self.clock)
        self.budget = Budget(
            TokenBucket(config.requests_per_second, self.clock),
            self.clock,
            interactive_per_minute=config.interactive_per_minute,
        )
        self._client = client

        self.items = ItemsRepo(self.conn)
        self.daily = DailyStatsRepo(self.conn)
        self.hourly = HourlyStatsRepo(self.conn)
        self.orders = OrderSnapshotsRepo(self.conn)
        self.raw_orders = RawSnapshotsRepo(self.conn)
        self.watchlist = WatchlistRepo(self.conn)
        self.groups = GroupsRepo(self.conn)
        self.signals = SignalsRepo(self.conn)
        self.trades = TradesRepo(self.conn)
        self.sweep_state = SweepStateRepo(self.conn)
        self.http_cache = HttpCacheRepo(self.conn)

    def new_client(self) -> WFMClient:
        if self._client is None:
            self._client = WFMClient(
                config=self.config,
                budget=self.budget,
                breaker=self.breaker,
                clock=self.clock,
                cache=self.http_cache,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        if self._owns_conn:
            self.conn.close()
