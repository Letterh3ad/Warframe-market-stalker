from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime

from wfm.api.errors import ApiError, CircuitOpen
from wfm.features import price as price_features
from wfm.services.alert_service import deliver, operational, run_digest
from wfm.services.analysis_service import analyze_item_records
from wfm.services.context import AppContext
from wfm.services.feature_service import anchor_date, market_context
from wfm.services.report_service import poll_book
from wfm.sync.budget import Priority
from wfm.sync.catalog import sync_catalog
from wfm.sync.scheduler import PollQueue, ScoreInputs, Weights, score
from wfm.sync.sweep import run_sweep

log = logging.getLogger(__name__)

IDLE_SLEEP_S = 60.0

# The queue's rebuild() call is a full watchlist read plus a full reheap. Doing it
# on every iteration (the brief's original shape) is a needless cost at 30+ minute
# poll intervals, so it is throttled to this cadence and forced only when the queue
# is empty (nothing else would ever pick up a first watchlist entry).
WATCHLIST_REFRESH_S = 60.0


@dataclass(frozen=True)
class DaemonReport:
    polls: int = 0
    sweeps: int = 0
    digests: int = 0
    halted: bool = False
    reason: str | None = None


class Daemon:
    """The adaptive poll loop, the daily sweep and the 09:00 digest as one loop.

    One loop rather than three tasks: with concurrency 1 they are never actually
    parallel, and a single loop keeps queue, budget and breaker state in one place
    without locking.
    """

    def __init__(self, ctx: AppContext, stop_event: asyncio.Event | None = None) -> None:
        self._ctx = ctx
        self._stop = stop_event or asyncio.Event()
        self._weights = Weights.from_config(ctx.config)
        self._queue = PollQueue(
            ctx.clock,
            floor_minutes=ctx.config.poll_floor_minutes,
            ceiling_minutes=ctx.config.poll_ceiling_minutes,
            decay_after=ctx.config.decay_after_unchanged_polls,
            saturation=ctx.config.score_saturation,
            state=ctx.poll_state,
            catchup_max_items=ctx.config.catchup_max_items,
        )
        self._market = None
        self._last_watchlist_refresh: float | None = None

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self, max_iterations: int | None = None) -> DaemonReport:
        ctx = self._ctx

        # A stop already on record (e.g. `wfm daemon stop` fired before this process
        # picked it up) is honoured without ever claiming the daemon started, rather
        # than clearing it via mark_started() below and running anyway.
        if ctx.daemon_state.stop_requested():
            ctx.daemon_state.mark_stopped(ctx.clock.utcnow())
            return DaemonReport()

        ctx.daemon_state.mark_started(pid=os.getpid(), when=ctx.clock.utcnow())
        polls = sweeps = digests = 0
        iterations = 0
        stop_requested = False

        while True:
            if self._stop.is_set() or ctx.daemon_state.stop_requested():
                stop_requested = True
                break
            if max_iterations is not None and iterations >= max_iterations:
                # Exhausting the iteration budget is not a stop: it is how a bounded
                # test run (and `wfm scan --once`) samples the loop. The daemon stays
                # "running" so a caller can start another bounded run right after.
                break
            iterations += 1
            now = ctx.clock.utcnow()

            try:
                if self._digest_due(now):
                    await run_digest(ctx)
                    ctx.daemon_state.mark_daily_done("digest", now.date())
                    digests += 1

                if self._sweep_due(now):
                    await self._run_sweep()
                    ctx.daemon_state.mark_daily_done("sweep", now.date())
                    sweeps += 1

                self._maybe_refresh_queue()
                item = self._queue.pop_due()
                if item is None:
                    await ctx.clock.sleep(self._queue.seconds_until_next() or IDLE_SLEEP_S)
                    continue

                await self.poll_once(item)
                polls += 1
                ctx.daemon_state.heartbeat(
                    ctx.clock.utcnow(), status="running", detail=f"polled {item.slug}"
                )
            except CircuitOpen as exc:
                ctx.daemon_state.heartbeat(ctx.clock.utcnow(), status="halted", detail=exc.reason)
                await operational(ctx, f"wfm daemon halted: {exc.reason}")
                return DaemonReport(polls, sweeps, digests, halted=True, reason=exc.reason)

        if stop_requested:
            ctx.daemon_state.mark_stopped(ctx.clock.utcnow())
        return DaemonReport(polls, sweeps, digests)

    async def poll_once(self, item) -> dict:
        ctx = self._ctx
        previous = ctx.orders.latest(item.slug, item.rank)
        rescheduled = False
        try:
            try:
                snapshot = await poll_book(ctx, item.slug, item.rank, priority=Priority.BACKGROUND)
            except CircuitOpen:
                # Escapes to end the run; matches the sweep's split from phase 3.
                raise
            except ApiError as exc:
                log.warning("poll of %s failed: %s", item.slug, exc)
                self._queue.reschedule(item, score_value=0.0, changed=False)
                rescheduled = True
                return {"slug": item.slug, "error": str(exc)}

            if self._market is None:
                self._market = market_context(ctx)
            payload, signals = analyze_item_records(
                ctx, item.slug, item.rank, snapshot=snapshot, market=self._market
            )
            if signals:
                await deliver(ctx, signals)

            changed = previous is None or (
                previous.best_ask,
                previous.best_bid,
                previous.ask_count,
                previous.bid_count,
            ) != (snapshot.best_ask, snapshot.best_bid, snapshot.ask_count, snapshot.bid_count)
            self._queue.reschedule(item, score_value=self._score_for(item, snapshot), changed=changed)
            rescheduled = True
            return payload
        finally:
            # Contract from task 3's review: pop_due() marks an item in-flight and
            # only reschedule() clears the mark. Any exit that has not already
            # rescheduled (an exception, including CircuitOpen) must still do so or
            # the item is stranded until a process restart.
            if not rescheduled:
                self._queue.reschedule(item, score_value=0.0, changed=False)

    def _score_for(self, item, snapshot) -> float:
        ctx = self._ctx
        # Anchored the same way feature_service.build_for anchors its own windows:
        # unanchored window() reads real wall-clock time and ignores ctx.clock, which
        # a FakeClock test would not catch.
        anchor = anchor_date(ctx, ctx.clock.utcnow())
        candles = ctx.daily.window(item.slug, item.rank, days=30, end=anchor)
        block, _ = price_features.build(candles, end=date.fromisoformat(anchor))
        spread_pct = None
        if snapshot.online_spread is not None and snapshot.online_best_ask:
            spread_pct = snapshot.online_spread / snapshot.online_best_ask
        return score(
            ScoreInputs(
                volatility=block.atr_pct,
                volume=block.median_volume_30d,
                online_spread_pct=spread_pct,
                pin_weight=item.pin_weight,
            ),
            self._weights,
        )

    async def _run_sweep(self) -> None:
        ctx = self._ctx
        await sync_catalog(ctx.new_client(), ctx.items, ctx.sweep_state, ctx.clock)
        await run_sweep(
            ctx.new_client(), ctx.items, ctx.daily, ctx.hourly, ctx.sweep_state, ctx.clock
        )
        # Rebuilt once per sweep, not per poll: it is a catalog-wide aggregate that
        # moves on a daily timescale.
        self._market = market_context(ctx)

    def _maybe_refresh_queue(self) -> None:
        now = self._ctx.clock.now()
        due = (
            self._queue.size == 0
            or self._last_watchlist_refresh is None
            or now - self._last_watchlist_refresh >= WATCHLIST_REFRESH_S
        )
        if not due:
            return
        self._queue.rebuild(self._ctx.watchlist.all())
        self._last_watchlist_refresh = now

    def _sweep_due(self, now: datetime) -> bool:
        ctx = self._ctx
        return now.hour >= ctx.config.sweep_hour and ctx.daemon_state.daily_done("sweep") != now.date()

    def _digest_due(self, now: datetime) -> bool:
        ctx = self._ctx
        return now.hour >= ctx.config.digest_hour and ctx.daemon_state.daily_done("digest") != now.date()
