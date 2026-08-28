import asyncio
from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.api.ratelimit import TokenBucket
from wfm.sync.budget import Budget, Priority

START = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _budget(rate: float = 2.0, interactive_per_minute: int = 3):
    clock = FakeClock(start_utc=START)
    bucket = TokenBucket(rate_per_second=rate, clock=clock)
    return Budget(bucket, clock, interactive_per_minute=interactive_per_minute), clock


async def test_acquire_paces_through_the_shared_bucket():
    budget, clock = _budget(rate=2.0)
    for _ in range(3):
        await budget.acquire(Priority.BULK)
    assert clock.now() == pytest.approx(1.0)


async def test_queued_waiters_are_served_by_priority_not_arrival():
    """The holder is never preempted (its request is already in flight), so the
    assertion is about the queue that forms behind it."""
    budget, clock = _budget(rate=2.0)
    served: list[str] = []

    async def take(label: str, priority: Priority) -> None:
        await budget.acquire(priority)
        served.append(label)

    await budget.acquire(Priority.BULK)  # so the next acquire has to wait, and a queue forms
    tasks = [
        asyncio.create_task(take("bulk", Priority.BULK)),
        asyncio.create_task(take("background", Priority.BACKGROUND)),
        asyncio.create_task(take("interactive", Priority.INTERACTIVE)),
    ]
    await asyncio.gather(*tasks)
    assert served == ["bulk", "interactive", "background"]
    assert clock.now() == pytest.approx(1.5)


async def test_priority_does_not_change_the_rate():
    budget, clock = _budget(rate=2.0)
    await asyncio.gather(
        *(
            asyncio.create_task(budget.acquire(p))
            for p in (Priority.BULK, Priority.INTERACTIVE, Priority.BACKGROUND)
        )
    )
    assert clock.now() == pytest.approx(1.0)


async def test_a_cancelled_waiter_does_not_strand_the_queue():
    budget, _ = _budget(rate=2.0)
    await budget.acquire(Priority.BULK)  # prime, so the next holder has to sleep
    holder = asyncio.create_task(budget.acquire(Priority.BULK))
    doomed = asyncio.create_task(budget.acquire(Priority.BULK))
    follower = asyncio.create_task(budget.acquire(Priority.INTERACTIVE))
    while len(budget._waiters) < 2:
        await asyncio.sleep(0)
    doomed.cancel()
    await asyncio.gather(holder, follower)
    assert doomed.cancelled()
    assert budget.total_spent == 3


async def test_spend_is_counted_per_class():
    budget, _ = _budget()
    await budget.acquire(Priority.BULK)
    await budget.acquire(Priority.BULK)
    await budget.acquire(Priority.INTERACTIVE)
    assert budget.spent(Priority.BULK) == 2
    assert budget.spent(Priority.INTERACTIVE) == 1
    assert budget.total_spent == 3


async def test_interactive_beyond_the_minute_cap_is_demoted_not_blocked():
    budget, clock = _budget(rate=2.0, interactive_per_minute=2)
    await budget.acquire(Priority.INTERACTIVE)
    await budget.acquire(Priority.INTERACTIVE)
    assert budget.interactive_remaining() == 0
    await budget.acquire(Priority.INTERACTIVE)
    assert budget.spent(Priority.INTERACTIVE) == 3
    assert clock.now() == pytest.approx(1.0)


async def test_the_interactive_cap_is_a_sliding_minute():
    budget, clock = _budget(rate=2.0, interactive_per_minute=2)
    await budget.acquire(Priority.INTERACTIVE)
    await budget.acquire(Priority.INTERACTIVE)
    clock.advance(61)
    assert budget.interactive_remaining() == 2


def test_reservations_reduce_what_background_may_spend():
    budget, _ = _budget(rate=2.0)
    assert budget.remaining_for(Priority.BACKGROUND, horizon_s=100) == 200
    budget.reserve("sweep", 150)
    assert budget.remaining_for(Priority.BACKGROUND, horizon_s=100) == 50
    assert budget.remaining_for(Priority.BULK, horizon_s=100) == 200
    budget.release_reservation("sweep")
    assert budget.remaining_for(Priority.BACKGROUND, horizon_s=100) == 200


def test_remaining_never_goes_negative():
    budget, _ = _budget(rate=2.0)
    budget.reserve("sweep", 10_000)
    assert budget.remaining_for(Priority.BACKGROUND, horizon_s=100) == 0
