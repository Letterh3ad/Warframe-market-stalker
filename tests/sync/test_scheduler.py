from datetime import datetime, timedelta, timezone

import pytest

from wfm.config import Config
from wfm.store.poll_state import PollStateRepo
from wfm.sync.scheduler import PollQueue, ScoreInputs, Weights, interval_minutes, score
from tests.fakes.clock import FakeClock
from wfm.models import WatchlistEntry

WEIGHTS = Weights.from_config(Config())


def _inputs(volatility=0.0, volume=0.0, spread=0.0, pin=0.0) -> ScoreInputs:
    return ScoreInputs(
        volatility=volatility, volume=volume, online_spread_pct=spread, pin_weight=pin
    )


def test_a_dead_item_scores_zero():
    assert score(_inputs(), WEIGHTS) == 0.0


def test_each_term_raises_the_score():
    base = score(_inputs(), WEIGHTS)
    assert score(_inputs(volatility=0.5), WEIGHTS) > base
    assert score(_inputs(volume=100), WEIGHTS) > base
    assert score(_inputs(spread=0.4), WEIGHTS) > base
    assert score(_inputs(pin=2.0), WEIGHTS) > base


def test_missing_inputs_count_as_zero_rather_than_raising():
    assert score(ScoreInputs(None, None, None, 0.0), WEIGHTS) == 0.0


def test_volume_is_compressed_so_one_whale_item_cannot_dominate():
    modest = score(_inputs(volume=10), WEIGHTS)
    huge = score(_inputs(volume=10_000), WEIGHTS)
    assert huge < modest * 5, "volume enters logarithmically"


def test_a_pin_is_the_strongest_single_lever():
    assert score(_inputs(pin=1.0), WEIGHTS) > score(_inputs(volatility=0.2), WEIGHTS)


def test_interval_of_a_zero_score_is_the_floor():
    assert interval_minutes(0.0, floor=30, ceiling=2) == 30


def test_interval_of_a_saturated_score_is_the_ceiling():
    assert interval_minutes(99.0, floor=30, ceiling=2, saturation=1.0) == 2


def test_interval_decreases_monotonically_with_score():
    intervals = [interval_minutes(s, floor=30, ceiling=2) for s in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert intervals == sorted(intervals, reverse=True)
    assert all(2 <= i <= 30 for i in intervals)


def test_interval_never_goes_below_the_ceiling_however_large_the_score():
    assert interval_minutes(10_000.0, floor=30, ceiling=2) == 2


START = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _entry(slug: str, pin: float = 0.0) -> WatchlistEntry:
    return WatchlistEntry(slug=slug, rank=0, added_at=START, pin_weight=pin)


def _queue(**kwargs):
    clock = FakeClock(start_utc=START)
    return PollQueue(clock, **kwargs), clock


def test_rebuild_makes_every_item_due_immediately():
    queue, _ = _queue()
    queue.rebuild([_entry("a"), _entry("b")])
    assert queue.size == 2
    assert queue.pop_due() is not None
    assert queue.pop_due() is not None
    assert queue.pop_due() is None


def test_a_polled_item_returns_at_the_floor_when_its_score_is_zero():
    queue, clock = _queue()
    queue.rebuild([_entry("a")])
    item = queue.pop_due()
    queue.reschedule(item, score_value=0.0, changed=True)
    assert queue.pop_due() is None
    assert queue.seconds_until_next() == pytest.approx(30 * 60)
    clock.advance(30 * 60)
    assert queue.pop_due().slug == "a"


def test_a_hot_item_returns_at_the_ceiling():
    queue, clock = _queue()
    queue.rebuild([_entry("a")])
    queue.reschedule(queue.pop_due(), score_value=5.0, changed=True)
    clock.advance(2 * 60)
    assert queue.pop_due().slug == "a"


def test_an_unchanged_book_decays_the_interval_back_toward_the_floor():
    queue, clock = _queue(decay_after=3)
    queue.rebuild([_entry("a")])
    intervals = []
    for _ in range(5):
        item = queue.pop_due()
        queue.reschedule(item, score_value=5.0, changed=False)
        intervals.append(queue.peek().interval_minutes)
        clock.advance(queue.seconds_until_next())
    assert intervals[0] == pytest.approx(2)
    assert intervals[-1] > intervals[0], "a book that never moves stops being polled hard"
    assert intervals[-1] <= 30


def test_a_pinned_items_decay_is_bounded_rather_than_reaching_the_floor():
    """Controller ruling on the task 4 review: decay used to override pin_weight
    entirely, dragging any unchanging item, pinned or not, to the 30 minute floor.
    That detects the first move on a pinned item up to a floor interval late, which
    is exactly the case a pin exists for."""
    queue, clock = _queue(decay_after=3)
    queue.rebuild([_entry("a", pin=3.0)])
    intervals = []
    for _ in range(8):
        item = queue.pop_due()
        queue.reschedule(item, score_value=5.0, changed=False)
        intervals.append(queue.peek().interval_minutes)
        clock.advance(queue.seconds_until_next())
    assert intervals[-1] < 30, "a pin keeps the item well short of the unpinned floor"
    assert intervals[-1] == pytest.approx(8.0), "capped at earned interval * PIN_DECAY_CAP_MULTIPLIER"


def test_a_change_resets_the_decay():
    queue, clock = _queue(decay_after=2)
    queue.rebuild([_entry("a")])
    for _ in range(3):
        queue.reschedule(queue.pop_due(), score_value=5.0, changed=False)
        clock.advance(queue.seconds_until_next())
    decayed = queue.peek().interval_minutes
    queue.reschedule(queue.pop_due(), score_value=5.0, changed=True)
    assert queue.peek().interval_minutes < decayed
    assert queue.peek().unchanged_polls == 0


def test_the_queue_orders_by_due_time_not_insertion():
    queue, clock = _queue()
    queue.rebuild([_entry("slow"), _entry("fast")])
    slow, fast = queue.pop_due(), queue.pop_due()
    if slow.slug != "slow":
        slow, fast = fast, slow
    queue.reschedule(slow, score_value=0.0, changed=True)
    queue.reschedule(fast, score_value=5.0, changed=True)
    clock.advance(30 * 60)
    assert queue.pop_due().slug == "fast"


def test_rebuild_drops_items_removed_from_the_watchlist():
    queue, _ = _queue()
    queue.rebuild([_entry("a"), _entry("b")])
    queue.rebuild([_entry("a")])
    assert queue.size == 1
    assert queue.pop_due().slug == "a"


def test_rebuild_preserves_the_schedule_of_items_that_remain():
    queue, clock = _queue()
    queue.rebuild([_entry("a")])
    queue.reschedule(queue.pop_due(), score_value=0.0, changed=True)
    queue.rebuild([_entry("a"), _entry("b")])
    assert queue.pop_due().slug == "b", "a is still on its 30 minute schedule"


def test_seconds_until_next_on_an_empty_queue_is_none():
    queue, _ = _queue()
    assert queue.seconds_until_next() is None


# --- Persistence: survive a restart / a host sleeping (addendum, binding) ---


def test_the_restore_across_a_restart_converts_wall_clock_to_monotonic(conn):
    """The pinned wall-clock <-> monotonic conversion test the addendum requires.

    A second queue is built with a different monotonic origin (as a real restart
    would have) and a wall clock 10 minutes further along. A naive implementation
    that reuses due_at verbatim, or that just marks everything due now, both fail
    this: the restored item must be due in ~20 of its original 30 minutes, not 30
    and not immediately.
    """
    state = PollStateRepo(conn)
    clock1 = FakeClock(start_utc=START, start_monotonic=100.0)
    queue1 = PollQueue(clock1, state=state)
    queue1.rebuild([_entry("a")])
    queue1.reschedule(queue1.pop_due(), score_value=0.0, changed=True)

    clock2 = FakeClock(start_utc=START + timedelta(minutes=10), start_monotonic=9999.0)
    queue2 = PollQueue(clock2, state=state)
    queue2.rebuild([_entry("a")])

    assert queue2.pop_due() is None, "not due immediately"
    assert queue2.seconds_until_next() == pytest.approx(20 * 60, abs=1)


def test_a_restart_resumes_the_stored_schedule_rather_than_a_cold_start(conn):
    state = PollStateRepo(conn)
    clock1 = FakeClock(start_utc=START)
    queue1 = PollQueue(clock1, state=state)
    queue1.rebuild([_entry("a")])
    queue1.reschedule(queue1.pop_due(), score_value=0.0, changed=True)

    clock2 = FakeClock(start_utc=START, start_monotonic=500.0)
    queue2 = PollQueue(clock2, state=state)
    queue2.rebuild([_entry("a")])

    assert queue2.pop_due() is None
    assert queue2.seconds_until_next() == pytest.approx(30 * 60)
    clock2.advance(30 * 60)
    assert queue2.pop_due().slug == "a"


def test_a_restart_after_a_long_sleep_makes_overdue_items_due_oldest_first(conn):
    """Wall-clock age must win regardless of watchlist order, not just insertion order.

    "a" is listed first in the watchlist, but "b" is scheduled first here, so "b"
    has the older stored due_at and is the more-starved item. catchup_max_items=1
    forces a real deferral choice: without the oldest-first sort, this degrades to
    heap tie-break order (insertion order via _counter, which tracks wanted-dict
    iteration order, i.e. watchlist order [a, b]), so "a" would wrongly stay due
    now and the genuinely older "b" would wrongly get deferred.
    """
    state = PollStateRepo(conn)
    clock1 = FakeClock(start_utc=START)
    queue1 = PollQueue(clock1, state=state)
    queue1.rebuild([_entry("a"), _entry("b")])
    first, second = queue1.pop_due(), queue1.pop_due()
    a, b = (first, second) if first.slug == "a" else (second, first)
    queue1.reschedule(b, score_value=0.0, changed=True)  # b scheduled first -> older due_at
    clock1.advance(5 * 60)
    queue1.reschedule(a, score_value=0.0, changed=True)  # a scheduled later -> newer due_at

    clock2 = FakeClock(start_utc=clock1.utcnow() + timedelta(hours=8), start_monotonic=0.0)
    queue2 = PollQueue(clock2, state=state, catchup_max_items=1)
    queue2.rebuild([_entry("a"), _entry("b")])  # watchlist order is [a, b], opposite of age order

    due_now = queue2.pop_due()
    assert due_now.slug == "b", "b was scheduled earlier, so it is more overdue, despite coming second in watchlist order"
    assert queue2.pop_due() is None, "a was deferred by the cap"


def test_bounded_catchup_caps_immediate_pops_and_spreads_the_rest(conn):
    state = PollStateRepo(conn)
    clock1 = FakeClock(start_utc=START)
    entries = [_entry(f"item{i}") for i in range(40)]
    queue1 = PollQueue(clock1, state=state)
    queue1.rebuild(entries)
    for _ in range(40):
        item = queue1.pop_due()
        queue1.reschedule(item, score_value=0.0, changed=True)
        clock1.advance(1)

    clock2 = FakeClock(start_utc=clock1.utcnow() + timedelta(hours=8), start_monotonic=0.0)
    queue2 = PollQueue(clock2, state=state, catchup_max_items=10)
    queue2.rebuild(entries)

    assert queue2.size == 40, "none of the 40 are lost"
    due_now = 0
    while queue2.pop_due() is not None:
        due_now += 1
    assert due_now == 10
    assert queue2.seconds_until_next() is not None
    assert queue2.seconds_until_next() <= 30 * 60, "deferred ones still land within the floor"


# --- In-flight bookkeeping: a rebuild or a crash must not touch the floor (I1) ---


def test_rebuild_during_an_inflight_poll_does_not_resurrect_it_below_the_floor():
    queue, clock = _queue()
    queue.rebuild([_entry("a")])
    item = queue.pop_due()  # "a" is checked out, as if the runner is mid-request
    queue.rebuild([_entry("a")])  # a concurrent watchlist refresh while "a" is still in flight
    assert queue.pop_due() is None, "an in-flight item must not be handed out a second time"
    queue.reschedule(item, score_value=0.0, changed=True)
    assert queue.pop_due() is None
    clock.advance(30 * 60)
    assert queue.pop_due().slug == "a"


def test_reschedule_after_a_rebuild_during_flight_leaves_no_duplicate_heap_entry():
    queue, clock = _queue()
    queue.rebuild([_entry("a")])
    item = queue.pop_due()
    queue.rebuild([_entry("a")])
    queue.reschedule(item, score_value=5.0, changed=True)  # hot score -> ceiling interval
    clock.advance(2 * 60)
    assert queue.pop_due().slug == "a"
    assert queue.pop_due() is None, "a stale duplicate heap entry would pop a a second time"


def test_an_item_popped_but_never_rescheduled_is_not_lost_forever(conn):
    state = PollStateRepo(conn)
    clock1 = FakeClock(start_utc=START)
    queue1 = PollQueue(clock1, state=state)
    queue1.rebuild([_entry("a")])
    queue1.reschedule(queue1.pop_due(), score_value=0.0, changed=True)
    clock1.advance(30 * 60)
    stuck = queue1.pop_due()
    assert stuck is not None  # the runner picks "a" up here, then crashes before reschedule

    clock2 = FakeClock(start_utc=clock1.utcnow(), start_monotonic=0.0)
    queue2 = PollQueue(clock2, state=state)
    queue2.rebuild([_entry("a")])

    assert queue2.pop_due() is not None, "a crash mid-poll must not strand the item forever"


def test_rebuild_deletes_the_stored_row_of_a_removed_item(conn):
    state = PollStateRepo(conn)
    clock = FakeClock(start_utc=START)
    queue = PollQueue(clock, state=state)
    queue.rebuild([_entry("a"), _entry("b")])
    queue.reschedule(queue.pop_due(), score_value=0.0, changed=True)
    queue.reschedule(queue.pop_due(), score_value=0.0, changed=True)
    assert state.get("a", 0) is not None

    queue.rebuild([_entry("b")])

    assert state.get("a", 0) is None
