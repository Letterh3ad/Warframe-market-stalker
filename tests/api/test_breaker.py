from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.api.breaker import CircuitBreaker
from wfm.api.errors import CircuitOpen

START = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _breaker() -> tuple[CircuitBreaker, FakeClock]:
    clock = FakeClock(start_utc=START)
    return CircuitBreaker(clock=clock, cooldown_s=900.0), clock


def test_starts_closed():
    breaker, _ = _breaker()
    breaker.check()
    assert breaker.is_open is False


def test_three_consecutive_429s_trip_it():
    breaker, _ = _breaker()
    breaker.record_429()
    breaker.record_429()
    breaker.check()
    breaker.record_429()
    assert breaker.is_open is True
    with pytest.raises(CircuitOpen) as excinfo:
        breaker.check()
    assert "429" in str(excinfo.value)


def test_a_success_resets_the_429_run():
    breaker, _ = _breaker()
    breaker.record_429()
    breaker.record_429()
    breaker.record_success()
    breaker.record_429()
    breaker.check()
    assert breaker.is_open is False


def test_five_consecutive_5xx_trip_it():
    breaker, _ = _breaker()
    for _ in range(4):
        breaker.record_5xx()
    breaker.check()
    breaker.record_5xx()
    assert breaker.is_open is True


def test_it_closes_only_after_the_cooldown():
    breaker, clock = _breaker()
    for _ in range(3):
        breaker.record_429()
    clock.advance(899)
    with pytest.raises(CircuitOpen):
        breaker.check()
    clock.advance(2)
    breaker.check()
    assert breaker.is_open is False


def test_the_reason_is_readable_for_sweep_state():
    breaker, _ = _breaker()
    for _ in range(3):
        breaker.record_429()
    assert "consecutive" in breaker.reason
