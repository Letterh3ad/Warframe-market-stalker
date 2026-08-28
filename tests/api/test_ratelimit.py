from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.api.ratelimit import TokenBucket
from wfm.config import MAX_REQUESTS_PER_SECOND

START = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _bucket(rate: float = 2.0) -> tuple[TokenBucket, FakeClock]:
    clock = FakeClock(start_utc=START)
    return TokenBucket(rate_per_second=rate, clock=clock), clock


async def test_first_acquire_is_immediate():
    bucket, clock = _bucket()
    await bucket.acquire()
    assert clock.now() == 0


async def test_requests_are_spaced_by_the_interval():
    bucket, clock = _bucket(rate=2.0)
    for _ in range(5):
        await bucket.acquire()
    assert clock.now() == pytest.approx(2.0)


async def test_idle_time_does_not_bank_a_burst():
    bucket, clock = _bucket(rate=2.0)
    await bucket.acquire()
    clock.advance(60)
    await bucket.acquire()
    await bucket.acquire()
    assert clock.now() == pytest.approx(60.5)


async def test_effective_rate_never_exceeds_the_configured_rate():
    bucket, clock = _bucket(rate=2.8)
    for _ in range(29):
        await bucket.acquire()
    # Tolerance, not sloppiness: the interval is added once per request, so the sum
    # lands a couple of ulps either side of the ideal. The drift is relative, so it
    # stays in the picoseconds over a full sweep.
    assert clock.now() >= 28 / 2.8 - 1e-9


def test_rate_above_the_published_ceiling_is_refused():
    clock = FakeClock(start_utc=START)
    with pytest.raises(ValueError):
        TokenBucket(rate_per_second=MAX_REQUESTS_PER_SECOND + 0.1, clock=clock)


def test_non_positive_rate_is_refused():
    clock = FakeClock(start_utc=START)
    with pytest.raises(ValueError):
        TokenBucket(rate_per_second=0, clock=clock)
