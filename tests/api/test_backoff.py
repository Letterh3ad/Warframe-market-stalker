from datetime import datetime, timedelta, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.api.backoff import delay_for, parse_retry_after

START = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def test_exponential_from_two_seconds():
    assert [delay_for(a) for a in (1, 2, 3, 4)] == [2.0, 4.0, 8.0, 16.0]


def test_capped_at_five_minutes():
    assert delay_for(20) == 300.0


def test_retry_after_wins_over_the_curve():
    assert delay_for(4, retry_after=1.0) == 1.0


def test_retry_after_seconds_is_parsed():
    assert parse_retry_after("120", FakeClock(start_utc=START)) == 120.0


def test_retry_after_http_date_is_parsed_as_a_delta():
    clock = FakeClock(start_utc=START)
    when = (START + timedelta(seconds=90)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(when, clock) == pytest.approx(90.0, abs=1.0)


def test_absent_or_junk_retry_after_is_none():
    clock = FakeClock(start_utc=START)
    assert parse_retry_after(None, clock) is None
    assert parse_retry_after("soon", clock) is None


def test_a_retry_after_in_the_past_is_clamped_to_zero():
    clock = FakeClock(start_utc=START)
    when = (START - timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(when, clock) == 0.0


def test_a_naive_retry_after_date_is_read_as_utc():
    """parsedate_to_datetime returns a naive datetime for the legal -0000 zone, and
    subtracting it from an aware now() raised TypeError out of the retry loop."""
    clock = FakeClock(start_utc=START)
    when = (START + timedelta(seconds=45)).strftime("%a, %d %b %Y %H:%M:%S -0000")
    assert parse_retry_after(when, clock) == pytest.approx(45.0, abs=1.0)


def test_a_zero_retry_after_still_leaves_a_pause():
    assert delay_for(1, retry_after=0.0) >= 1.0


def test_a_non_finite_retry_after_falls_back_to_the_curve():
    assert delay_for(3, retry_after=float("inf")) == 8.0
    assert delay_for(3, retry_after=float("nan")) == 8.0


def test_an_absurd_retry_after_falls_back_to_the_curve():
    assert delay_for(1, retry_after=60 * 60 * 48) == 2.0
