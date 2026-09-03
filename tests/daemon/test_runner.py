from datetime import datetime, timezone

import pytest

from tests.fakes.api import StubClient
from tests.fakes.clock import FakeClock
from wfm.api.errors import CircuitOpen
from wfm.config import Config
from wfm.daemon.runner import Daemon
from wfm.models import DailyCandle, Item
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


async def test_a_pinned_item_polls_far_more_often_than_an_unpinned_one(ctx):
    ctx.items.upsert_many([Item(slug="b", name="B", url_name="b", tags=("mod",))])
    ctx.daily.upsert_many(
        [DailyCandle(slug="b", rank=0, date=f"2026-06-{d:02d}", close=50, high=52, low=48,
                     median=50, volume=30) for d in range(1, 31)]
    )
    ctx.watchlist.add("b", 0, START, pin_weight=3.0)
    # Short window: unchanged-poll decay (identical book data every poll) pulls both
    # items' intervals toward the floor after a handful of polls, which would erase
    # the pin's advantage over a long run. The gap is real early on.
    report = await Daemon(ctx).run(max_iterations=12)
    counts: dict[str, int] = {}
    for url, _ in ctx.new_client().calls:
        if "/orders/item/" in url:
            slug = url.rstrip("/").split("/")[-1]
            counts[slug] = counts.get(slug, 0) + 1
    assert counts["b"] > counts["a"] * 2
    assert report.polls == sum(counts.values())


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
    await Daemon(ctx).run(max_iterations=2)
    state = ctx.daemon_state.get()
    assert state["status"] == "running"
    assert state["heartbeat_at"] is not None


async def test_signals_from_a_poll_are_delivered(ctx, monkeypatch):
    delivered = []

    async def fake_deliver(context, signals, sinks=None):
        delivered.extend(signals)
        return []

    monkeypatch.setattr("wfm.daemon.runner.deliver", fake_deliver)
    await Daemon(ctx).run(max_iterations=2)
    assert isinstance(delivered, list)  # zero is fine on flat data, the wiring is what matters


async def test_a_stop_request_already_set_returns_without_polling(ctx):
    ctx.daemon_state.request_stop(ctx.clock.utcnow())
    report = await Daemon(ctx).run(max_iterations=5)
    assert report.polls == 0
    assert ctx.daemon_state.get()["status"] == "stopped"


async def test_the_stop_event_ends_the_loop_cleanly(ctx):
    daemon = Daemon(ctx)
    daemon.request_stop()
    report = await daemon.run(max_iterations=5)
    assert report.polls == 0
    assert ctx.daemon_state.get()["status"] == "stopped"
