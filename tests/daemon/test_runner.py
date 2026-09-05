import asyncio
from datetime import datetime, timezone

import pytest

from tests.fakes.api import StubClient
from tests.fakes.clock import FakeClock
from wfm.api.errors import ApiError, CircuitOpen
from wfm.config import Config
from wfm.daemon.runner import Daemon
from wfm.models import DailyCandle, Direction, Horizon, Item, Signal
from wfm.services.context import AppContext
from wfm.sync.budget import Priority

# 05:00, not the brief's 10:00: sweep_hour=4 and digest_hour=9 would otherwise both
# already be "due" on iteration 1 of every test (see addendum item 3).
START = datetime(2026, 8, 27, 5, 0, tzinfo=timezone.utc)

ORDERS = [
    {"platinum": 45, "quantity": 2, "rank": 0, "type": "sell", "visible": True,
     "user": {"status": "ingame"}, "updatedAt": "2026-08-27T04:00:00Z"},
    {"platinum": 40, "quantity": 3, "rank": 0, "type": "buy", "visible": True,
     "user": {"status": "online"}, "updatedAt": "2026-08-27T04:00:00Z"},
]
VERSIONS = {"collections": {"items": "v42"}}
STATS = {"payload": {"statistics_closed": {"90days": [], "48hours": []}}}


@pytest.fixture
def ctx(conn):
    client = StubClient(
        {"/orders/item/": ORDERS, "/versions": VERSIONS, "/items": [], "/statistics": STATS}
    )
    context = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=START), client=client)
    context.items.upsert_many([Item(slug="a", name="A", url_name="a", tags=("mod",))])
    context.daily.upsert_many(
        [DailyCandle(slug="a", rank=0, date=f"2026-06-{d:02d}", close=50, high=52, low=48,
                     median=50, volume=30) for d in range(1, 31)]
    )
    context.watchlist.add("a", 0, START)
    # Seed today's sweep as already done so it does not fire incidentally in tests
    # that are not about the sweep; the digest is left undone since digest_hour (9)
    # is already past START's hour (5) is false, so it would not fire anyway.
    context.daemon_state.mark_started(pid=1, when=START)
    context.daemon_state.mark_daily_done("sweep", START.date())
    return context


async def test_a_single_iteration_polls_the_due_item_and_stores_a_snapshot(ctx):
    report = await Daemon(ctx).run(max_iterations=1)
    assert report.polls == 1
    assert ctx.orders.latest("a", 0).online_best_ask == 45


async def test_poll_requests_are_background_priority(ctx):
    await Daemon(ctx).run(max_iterations=1)
    order_calls = [c for c in ctx.new_client().calls if "/orders/item/" in c[0]]
    assert order_calls[0][1] is Priority.BACKGROUND


async def test_the_thirty_minute_floor_holds_for_a_quiet_item(ctx):
    report = await Daemon(ctx).run(max_iterations=6)
    elapsed_hours = (ctx.clock.utcnow() - START).total_seconds() / 3600
    assert report.polls <= elapsed_hours * 2 + 1, "no faster than the floor allows on a flat item"


def _add_item_b(ctx, pin_weight: float) -> None:
    ctx.items.upsert_many([Item(slug="b", name="B", url_name="b", tags=("mod",))])
    ctx.daily.upsert_many(
        [DailyCandle(slug="b", rank=0, date=f"2026-06-{d:02d}", close=50, high=52, low=48,
                     median=50, volume=30) for d in range(1, 31)]
    )
    ctx.watchlist.add("b", 0, START, pin_weight=pin_weight)


def _poll_counts(ctx) -> dict[str, int]:
    counts: dict[str, int] = {}
    for url, _ in ctx.new_client().calls:
        if "/orders/item/" in url:
            slug = url.rstrip("/").split("/")[-1]
            counts[slug] = counts.get(slug, 0) + 1
    return counts


async def test_a_pinned_item_polls_far_more_often_early_in_a_run(ctx):
    """This is an early-run property, not a standing one: see the companion
    convergence test below. Named for what it actually proves, per review."""
    _add_item_b(ctx, pin_weight=3.0)
    # Short window: unchanged-poll decay (identical book data every poll) pulls both
    # items' intervals toward the floor after a handful of polls, which narrows the
    # pin's advantage. The gap is wide early on, before decay has caught up.
    report = await Daemon(ctx).run(max_iterations=12)
    counts = _poll_counts(ctx)
    assert counts["b"] > counts["a"] * 2
    assert report.polls == sum(counts.values())


async def test_an_unpinned_item_settles_at_the_floor_while_a_pinned_one_stays_below_it(ctx):
    """Controller ruling on the task 4 review: decay is now bounded by pin_weight, so
    a pinned item's interval no longer decays all the way to the 30 minute floor like
    an unpinned item's does. Long-run behaviour, the complement of the test above."""
    _add_item_b(ctx, pin_weight=3.0)
    await Daemon(ctx).run(max_iterations=400)
    interval = {slug: ctx.poll_state.get(slug, 0)["interval_minutes"] for slug in ("a", "b")}
    assert interval["a"] == pytest.approx(ctx.config.poll_floor_minutes)
    assert interval["b"] < interval["a"], "the pin keeps b polling more often, even long-run"


async def test_a_tripped_breaker_halts_every_task_and_records_the_reason(conn):
    # A second AppContext with an error-raising client, per addendum item 7, rather
    # than reassigning ctx._client on an already-built context.
    stub = StubClient({"/versions": VERSIONS}, errors={"/orders/item/": CircuitOpen("3 consecutive 429s")})
    context = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=START), client=stub)
    context.items.upsert_many([Item(slug="a", name="A", url_name="a", tags=("mod",))])
    context.watchlist.add("a", 0, START)
    context.daemon_state.mark_started(pid=1, when=START)
    context.daemon_state.mark_daily_done("sweep", START.date())

    report = await Daemon(context).run(max_iterations=5)
    assert report.halted is True
    assert "429" in report.reason
    assert report.polls == 0
    assert "429" in context.daemon_state.get()["detail"]


async def test_a_raise_inside_a_poll_still_reschedules_the_item(conn):
    """Contract from task 3's review: pop_due() marks an item in-flight and only
    reschedule() clears that mark. poll_once must reschedule on every exit path,
    including an exception, or the item is stranded until a restart."""
    stub = StubClient({"/versions": VERSIONS}, errors={"/orders/item/": CircuitOpen("boom")})
    context = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=START), client=stub)
    context.items.upsert_many([Item(slug="a", name="A", url_name="a", tags=("mod",))])
    context.watchlist.add("a", 0, START)

    daemon = Daemon(context)
    daemon._queue.rebuild(context.watchlist.all())
    item = daemon._queue.pop_due()
    assert item is not None

    with pytest.raises(CircuitOpen):
        await daemon.poll_once(item)

    # Without a restart: same queue instance, just the clock moving past the
    # rescheduled due_at.
    context.clock.advance(context.config.poll_floor_minutes * 60)
    daemon._queue.rebuild(context.watchlist.all())
    assert daemon._queue.pop_due() is not None


async def test_the_digest_fires_once_when_its_hour_arrives(ctx, monkeypatch):
    fired = []

    async def fake_digest(context, sinks=None):
        fired.append(context.clock.utcnow())
        return {"delivered": 0, "sinks": []}

    monkeypatch.setattr("wfm.daemon.runner.run_digest", fake_digest)
    ctx.clock.advance(28 * 60 * 60)  # day 2, 09:00 UTC
    report = await Daemon(ctx).run(max_iterations=40)
    assert report.digests == 1
    assert fired[0].hour == ctx.config.digest_hour


async def test_a_second_run_the_same_day_does_not_refire_the_digest(ctx, monkeypatch):
    fired = []

    async def fake_digest(context, sinks=None):
        fired.append(context.clock.utcnow())
        return {"delivered": 0, "sinks": []}

    monkeypatch.setattr("wfm.daemon.runner.run_digest", fake_digest)
    ctx.clock.advance(28 * 60 * 60)
    await Daemon(ctx).run(max_iterations=40)
    report = await Daemon(ctx).run(max_iterations=5)
    assert report.digests == 0
    assert len(fired) == 1


async def test_a_transient_api_error_in_the_digest_leaves_the_daemon_running(ctx, monkeypatch):
    """Same shape as I3, on the digest: one flaky send at 09:00 must not reach the
    blanket except Exception and halt the daemon for the rest of the day."""

    async def flaky(context, sinks=None):
        raise ApiError("connect timeout")

    monkeypatch.setattr("wfm.daemon.runner.run_digest", flaky)
    ctx.clock.advance(28 * 60 * 60)  # day 2, 09:00 UTC

    report = await Daemon(ctx).run(max_iterations=2)

    assert report.halted is False
    assert report.digests == 0
    # Left unmarked deliberately: a later iteration retries the digest.
    assert ctx.daemon_state.daily_done("digest") != ctx.clock.utcnow().date()


async def test_a_tripped_breaker_in_the_digest_still_halts_the_daemon(ctx, monkeypatch):
    """The other half: the ApiError tolerance must not swallow a CircuitOpen, which
    is an ApiError subclass. A tripped breaker still halts."""

    async def tripped(context, sinks=None):
        raise CircuitOpen("3 consecutive 429s")

    monkeypatch.setattr("wfm.daemon.runner.run_digest", tripped)
    monkeypatch.setattr("wfm.daemon.runner.operational", _swallow_alert)
    ctx.clock.advance(28 * 60 * 60)  # day 2, 09:00 UTC

    report = await Daemon(ctx).run(max_iterations=2)

    assert report.halted is True
    assert "429" in report.reason


async def test_the_sweep_runs_in_its_configured_window_at_bulk_priority(ctx):
    ctx.clock.advance(23 * 60 * 60)  # day 2, 04:00 UTC
    report = await Daemon(ctx).run(max_iterations=30)
    sweep_calls = [c for c in ctx.new_client().calls if "/versions" in c[0]]
    assert sweep_calls
    assert all(priority is Priority.BULK for _, priority in sweep_calls)
    assert report.sweeps == 1


async def test_a_second_run_the_same_day_does_not_rerun_the_sweep(ctx):
    ctx.clock.advance(23 * 60 * 60)
    await Daemon(ctx).run(max_iterations=30)
    calls_before = len(ctx.new_client().calls)
    report = await Daemon(ctx).run(max_iterations=5)
    assert report.sweeps == 0
    # Still allowed to poll/call other endpoints, just no extra /versions traffic.
    versions_after = [c for c in ctx.new_client().calls[calls_before:] if "/versions" in c[0]]
    assert not versions_after


async def test_a_watchlist_change_is_picked_up_without_a_restart(ctx):
    daemon = Daemon(ctx)
    await daemon.run(max_iterations=1)
    ctx.items.upsert_many([Item(slug="c", name="C", url_name="c")])
    ctx.watchlist.add("c", 0, ctx.clock.utcnow())
    # Past the queue's watchlist-refresh throttle so the addition is actually seen.
    ctx.clock.advance(61)
    await daemon.run(max_iterations=3)
    polled = {url.rstrip("/").split("/")[-1] for url, _ in ctx.new_client().calls
              if "/orders/item/" in url}
    assert "c" in polled


async def test_an_empty_watchlist_sleeps_rather_than_spinning(ctx):
    ctx.watchlist.remove("a", 0)
    report = await Daemon(ctx).run(max_iterations=3)
    assert report.polls == 0
    assert ctx.clock.now() > 0, "the loop slept rather than burning iterations"


async def test_a_heartbeat_is_written_each_iteration(ctx):
    # Fixture already calls mark_started(when=START), which sets heartbeat_at; the
    # assertion has to move past START and pin the poll-path detail text to be able
    # to fail if the per-iteration write were removed (review finding C1).
    await Daemon(ctx).run(max_iterations=1)
    state = ctx.daemon_state.get()
    assert state["status"] == "running"
    assert state["heartbeat_at"] is not None
    # mark_started() also sets status="running" with detail=None; only a real
    # per-iteration heartbeat() call sets this detail, so this is what makes the
    # test able to fail if that call were deleted.
    assert state["detail"] == "polled a"


async def test_a_heartbeat_advances_on_the_idle_path_too(ctx):
    ctx.watchlist.remove("a", 0)
    await Daemon(ctx).run(max_iterations=2)
    state = ctx.daemon_state.get()
    assert state["heartbeat_at"] > START
    assert state["detail"] == "idle"


async def test_signals_from_a_poll_are_delivered(ctx, monkeypatch):
    fake_signal = Signal(
        slug="a", rank=0, analyzer="flip", ts=ctx.clock.utcnow(), direction=Direction.BUY,
        magnitude=1.0, confidence=0.9, horizon=Horizon.URGENT, id=99,
    )

    def fake_records(context, slug, rank, snapshot=None, market=None, now=None, persist=True):
        payload = {"slug": slug, "rank": rank, "signals": [], "skipped": [], "suppressed": []}
        return payload, [fake_signal]

    monkeypatch.setattr("wfm.daemon.runner.analyze_item_records", fake_records)

    delivered = []

    async def fake_deliver(context, signals, sinks=None):
        delivered.extend(signals)
        return []

    monkeypatch.setattr("wfm.daemon.runner.deliver", fake_deliver)
    await Daemon(ctx).run(max_iterations=1)
    # Deleting the `if signals: await deliver(...)` call in poll_once must fail this.
    assert delivered == [fake_signal]
    assert delivered[0].id == 99


async def test_signals_from_a_poll_are_published_to_the_broadcaster(ctx, monkeypatch):
    fake_signal = Signal(
        slug="a", rank=0, analyzer="flip", ts=ctx.clock.utcnow(), direction=Direction.BUY,
        magnitude=1.0, confidence=0.9, horizon=Horizon.URGENT, id=99,
    )

    def fake_records(context, slug, rank, snapshot=None, market=None, now=None, persist=True):
        payload = {"slug": slug, "rank": rank, "signals": [], "skipped": [], "suppressed": []}
        return payload, [fake_signal]

    monkeypatch.setattr("wfm.daemon.runner.analyze_item_records", fake_records)

    async def fake_deliver(context, signals, sinks=None):
        return []

    monkeypatch.setattr("wfm.daemon.runner.deliver", fake_deliver)
    queue = ctx.broadcaster.subscribe()

    await Daemon(ctx).run(max_iterations=1)

    published = queue.get_nowait()
    assert published["slug"] == "a"
    assert published["analyzer"] == "flip"


async def test_an_unexpected_exception_halts_the_daemon_with_an_alert(ctx, monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("bad candle")

    monkeypatch.setattr("wfm.daemon.runner.analyze_item_records", boom)
    alerts = []

    async def fake_operational(context, message, sinks=None):
        alerts.append(message)
        return []

    monkeypatch.setattr("wfm.daemon.runner.operational", fake_operational)
    report = await Daemon(ctx).run(max_iterations=5)
    assert report.halted is True
    assert "bad candle" in report.reason
    assert ctx.daemon_state.get()["status"] == "halted"
    assert alerts


async def test_a_stop_request_already_set_returns_without_polling(ctx):
    ctx.daemon_state.request_stop(ctx.clock.utcnow())
    report = await Daemon(ctx).run(max_iterations=5)
    assert report.polls == 0
    assert report.reason == "stop requested"
    assert ctx.daemon_state.get()["status"] == "stopped"


async def test_the_stop_event_ends_the_loop_cleanly(ctx):
    daemon = Daemon(ctx)
    daemon.request_stop()
    report = await daemon.run(max_iterations=5)
    assert report.polls == 0
    assert ctx.daemon_state.get()["status"] == "stopped"


async def test_a_stop_event_interrupts_a_long_idle_sleep_within_one_chunk(ctx):
    """review I1: seconds_until_next() can be up to the 30 minute floor; the loop must
    not sleep it out once a stop is requested mid-wait."""
    ctx.watchlist.remove("a", 0)  # nothing due, so the idle branch sleeps IDLE_SLEEP_S
    daemon = Daemon(ctx)

    async def request_stop_after_one_chunk():
        await asyncio.sleep(0)
        daemon.request_stop()

    # Retained: an unreferenced task can be garbage collected mid-flight.
    stopper = asyncio.create_task(request_stop_after_one_chunk())
    report = await daemon.run(max_iterations=100)
    await stopper
    assert report.polls == 0
    # Far less than the full 60s idle sleep elapsed: the chunk cap bounded the wait
    # rather than sleeping it out before the stop was even checked again.
    assert ctx.clock.now() < 60.0


async def test_sleep_until_next_does_not_sleep_at_all_once_already_stopped(ctx):
    daemon = Daemon(ctx)
    daemon.request_stop()
    await daemon._sleep_until_next(1800.0, "idle")
    assert ctx.clock.now() == 0.0


async def test_a_mid_run_flag_stop_ends_the_loop_on_the_poll_path(ctx, monkeypatch):
    """Round 2 regression test (N1): the per-iteration heartbeat wrote status=
    "running" unconditionally, which erased a "stopping" flag written by another
    process mid-run before the loop-top check ever saw it. request_stop() fires right
    after the first poll completes, exactly where the old heartbeat call ran next."""
    original_poll_once = Daemon.poll_once

    async def poll_once_then_flag_stop(self, item):
        result = await original_poll_once(self, item)
        ctx.daemon_state.request_stop(ctx.clock.utcnow())
        return result

    monkeypatch.setattr(Daemon, "poll_once", poll_once_then_flag_stop)
    report = await Daemon(ctx).run(max_iterations=20)
    assert ctx.daemon_state.get()["status"] == "stopped"
    assert report.polls == 1


async def test_a_mid_run_flag_stop_ends_the_loop_on_the_idle_path(ctx, monkeypatch):
    """Round 2 regression test (N1), idle-path half. request_stop() fires right after
    the first sleep chunk completes, before that chunk's own heartbeat call."""
    ctx.watchlist.remove("a", 0)  # nothing due, so every iteration takes the idle branch
    original_sleep = ctx.clock.sleep
    fired = {"once": False}

    async def sleep_then_flag_stop(seconds):
        await original_sleep(seconds)
        if not fired["once"]:
            fired["once"] = True
            ctx.daemon_state.request_stop(ctx.clock.utcnow())

    monkeypatch.setattr(ctx.clock, "sleep", sleep_then_flag_stop)
    report = await Daemon(ctx).run(max_iterations=100)
    assert ctx.daemon_state.get()["status"] == "stopped"
    assert report.polls == 0


async def test_the_heartbeat_cadence_is_independent_of_the_sleep_length(ctx):
    """N2: heartbeat_at must not be bounded by seconds_until_next(). A quiet,
    non-empty watchlist used to advance it only once per floor interval, up to 30
    minutes, which is C1's original failure scenario (a wedged loop and a quiet loop
    look identical for that whole stretch)."""
    daemon = Daemon(ctx)
    await daemon.run(max_iterations=1)  # polls "a", schedules its next due far out

    calls: list = []
    original_heartbeat = ctx.daemon_state.heartbeat

    def recording_heartbeat(when, status="running", detail=None):
        calls.append(when)
        return original_heartbeat(when, status=status, detail=detail)

    ctx.daemon_state.heartbeat = recording_heartbeat
    await daemon.run(max_iterations=1)  # idle: sleeps the whole gap to "a"'s next due
    assert len(calls) > 1, "one long idle wait must still heartbeat more than once"
    assert calls == sorted(calls), "each heartbeat call is later than the last"


async def test_a_foreground_run_does_not_claim_daemon_identity_when_own_state_is_false(ctx):
    """review I2: `wfm scan --once` runs Daemon.run() in a separate, short-lived
    process against the same DB as a possibly-running real daemon. own_state=False
    means it must not overwrite pid/status/heartbeat, which the real daemon owns."""
    ctx.daemon_state.heartbeat(START, status="running", detail="real daemon polling")
    before = ctx.daemon_state.get()
    report = await Daemon(ctx, own_state=False).run(max_iterations=1)
    after = ctx.daemon_state.get()
    assert report.polls == 1
    assert after == before


async def test_a_foreground_run_does_not_swallow_a_pending_stop_when_own_state_is_false(ctx):
    ctx.daemon_state.request_stop(ctx.clock.utcnow())
    report = await Daemon(ctx, own_state=False).run(max_iterations=1)
    # The real daemon's own process is the only one allowed to mark itself stopped.
    assert ctx.daemon_state.get()["status"] == "stopping"
    assert report.reason == "stop requested"


async def test_a_foreground_run_never_executes_the_sweep_or_digest_when_own_state_is_false(ctx):
    """N3: own_state=False used to only skip the mark_daily_done ledger write, while
    the sweep and digest branches still ran (sync_catalog/run_sweep issuing thousands
    of BULK requests from a short-lived CLI process). The branches themselves must be
    gated, not just the write recording that they ran."""
    ctx.clock.advance(28 * 60 * 60)  # day 2, past both sweep_hour and digest_hour
    report = await Daemon(ctx, own_state=False).run(max_iterations=5)
    assert report.sweeps == 0
    assert report.digests == 0
    sweep_calls = [c for c in ctx.new_client().calls if "/versions" in c[0]]
    assert not sweep_calls


# --- final review: the sweep owns the loop for 20+ minutes, so it must not go dark ---


def _seed_catalog(ctx, count: int) -> None:
    ctx.items.upsert_many(
        [Item(slug=f"s{i}", name=f"S{i}", url_name=f"s{i}") for i in range(count)]
    )


def _advance_to_the_sweep_window(ctx) -> None:
    ctx.clock.advance(23 * 60 * 60)  # day 2, 04:00 UTC


async def _noop_backfill(*args, **kwargs):
    return None


async def _swallow_alert(context, message, sinks=None):
    return []


async def test_the_sweep_heartbeats_as_it_walks_the_catalog(ctx, monkeypatch):
    """I1: the sweep used to run inline with no progress callback, so heartbeat_at did
    not advance for its whole ~23 minute runtime and `wfm daemon status` reported the
    daemon stale every day at exactly its busiest moment."""
    _seed_catalog(ctx, 5)
    monkeypatch.setattr("wfm.sync.sweep.backfill_item", _noop_backfill)
    details: list[str | None] = []
    original_heartbeat = ctx.daemon_state.heartbeat

    def recording_heartbeat(when, status="running", detail=None):
        details.append(detail)
        return original_heartbeat(when, status=status, detail=detail)

    ctx.daemon_state.heartbeat = recording_heartbeat
    _advance_to_the_sweep_window(ctx)

    report = await Daemon(ctx).run(max_iterations=1)

    assert report.sweeps == 1
    assert len([d for d in details if d and d.startswith("sweep ")]) >= 5


async def test_a_stop_requested_during_the_sweep_ends_the_run_inside_it(ctx, monkeypatch):
    """I1: stop_requested() was only read at the top of an iteration, so a stop landing
    during the sweep waited out every remaining item (20+ minutes in production) while
    `wfm daemon stop` had already told the user the daemon exits after its current
    poll."""
    _seed_catalog(ctx, 20)
    swept: list[str] = []

    async def backfill_then_request_stop(client, slug, *args, **kwargs):
        swept.append(slug)
        ctx.daemon_state.request_stop(ctx.clock.utcnow())

    monkeypatch.setattr("wfm.sync.sweep.backfill_item", backfill_then_request_stop)
    _advance_to_the_sweep_window(ctx)

    report = await Daemon(ctx).run(max_iterations=5)

    assert report.halted is False
    assert len(swept) <= 2, "the stop was seen inside the sweep, not after all 21 items"
    assert ctx.daemon_state.get()["status"] == "stopped"
    assert report.sweeps == 0
    assert ctx.daemon_state.daily_done("sweep") != ctx.clock.utcnow().date()


async def test_a_breaker_halted_sweep_is_not_recorded_as_the_days_sweep(ctx, monkeypatch):
    """I2: run_sweep returns SweepResult(halted=True) rather than raising, and the
    runner discarded it, so a sweep that stopped 800 items in still marked the day
    done and the remaining ~3,000 items waited until tomorrow."""
    _seed_catalog(ctx, 3)

    async def tripped(*args, **kwargs):
        raise CircuitOpen("3 consecutive 429s")

    monkeypatch.setattr("wfm.sync.sweep.backfill_item", tripped)
    _advance_to_the_sweep_window(ctx)

    report = await Daemon(ctx).run(max_iterations=1)

    assert report.sweeps == 0
    assert ctx.daemon_state.daily_done("sweep") != ctx.clock.utcnow().date()
    assert report.polls == 1, "the poll loop is unaffected by the sweep's own halt"


async def test_a_transient_api_error_in_the_sweep_leaves_the_daemon_running(ctx, monkeypatch):
    """I3: one flaky /versions call at 04:00 reached the blanket except Exception,
    which halted the daemon for good and cost the whole night's polling."""

    async def flaky(*args, **kwargs):
        raise ApiError("connect timeout")

    monkeypatch.setattr("wfm.daemon.runner.sync_catalog", flaky)
    _advance_to_the_sweep_window(ctx)

    report = await Daemon(ctx).run(max_iterations=2)

    assert report.halted is False
    assert report.sweeps == 0
    assert report.polls >= 1, "the night's polling continues"
    # Left unmarked deliberately: a later iteration retries the sweep.
    assert ctx.daemon_state.daily_done("sweep") != ctx.clock.utcnow().date()


async def test_a_tripped_breaker_in_the_catalog_sync_still_halts_the_daemon(ctx, monkeypatch):
    """The other half of I3: the ApiError tolerance must not swallow a CircuitOpen,
    which is an ApiError subclass. A tripped breaker still halts."""

    async def tripped(*args, **kwargs):
        raise CircuitOpen("3 consecutive 429s")

    monkeypatch.setattr("wfm.daemon.runner.sync_catalog", tripped)
    monkeypatch.setattr("wfm.daemon.runner.operational", _swallow_alert)
    _advance_to_the_sweep_window(ctx)

    report = await Daemon(ctx).run(max_iterations=2)

    assert report.halted is True
    assert "429" in report.reason

