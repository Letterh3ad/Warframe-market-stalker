from datetime import datetime, timezone

import httpx
import pytest

from tests.fakes.clock import FakeClock
from wfm.api.breaker import CircuitBreaker
from wfm.api.client import WFMClient
from wfm.api.errors import ApiError, CircuitOpen
from wfm.api.ratelimit import TokenBucket
from wfm.config import Config
from wfm.store.http_cache import HttpCacheRepo
from wfm.sync.budget import Budget, Priority

START = datetime(2026, 8, 27, tzinfo=timezone.utc)
URL = "https://api.warframe.market/v2/items"


def _client(handler, cache: HttpCacheRepo | None = None) -> tuple[WFMClient, FakeClock, list]:
    clock = FakeClock(start_utc=START)
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    budget = Budget(TokenBucket(2.0, clock), clock)
    client = WFMClient(
        config=Config(),
        budget=budget,
        breaker=CircuitBreaker(clock=clock),
        clock=clock,
        cache=cache,
        transport=httpx.MockTransport(recording),
    )
    return client, clock, seen


async def test_get_json_unwraps_the_data_envelope():
    client, _, _ = _client(lambda r: httpx.Response(200, json={"data": [{"slug": "x"}]}))
    assert await client.get_json(URL) == [{"slug": "x"}]
    await client.aclose()


async def test_a_bare_payload_is_returned_as_is():
    client, _, _ = _client(lambda r: httpx.Response(200, json={"apiVersion": "2.0"}))
    assert await client.get_json(URL) == {"apiVersion": "2.0"}
    await client.aclose()


async def test_required_headers_are_sent():
    client, _, seen = _client(lambda r: httpx.Response(200, json={"data": {}}))
    await client.get_json(URL)
    headers = seen[0].headers
    assert headers["user-agent"].startswith("WFMStalker/")
    assert "(+http" in headers["user-agent"]
    assert "gzip" in headers["accept-encoding"]
    assert headers["accept"] == "application/json"
    await client.aclose()


async def test_the_client_exposes_no_write_verbs():
    client, _, _ = _client(lambda r: httpx.Response(200, json={"data": {}}))
    for verb in ("post", "put", "patch", "delete"):
        assert not hasattr(client, verb)
    await client.aclose()


async def test_requests_are_paced_by_the_budget():
    client, clock, _ = _client(lambda r: httpx.Response(200, json={"data": {}}))
    for _ in range(3):
        await client.get_json(URL)
    assert clock.now() == pytest.approx(1.0)
    await client.aclose()


async def test_429_is_retried_after_the_retry_after_header():
    responses = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json={"data": {"ok": True}}),
    ]
    client, clock, _ = _client(lambda r: responses.pop(0))
    assert await client.get_json(URL) == {"ok": True}
    assert 7 in clock.sleeps
    await client.aclose()


async def test_three_consecutive_429s_trip_the_breaker_and_stop_retrying():
    client, _, seen = _client(lambda r: httpx.Response(429))
    with pytest.raises(CircuitOpen):
        await client.get_json(URL)
    assert len(seen) == 3
    await client.aclose()


async def test_5xx_is_retried_with_exponential_backoff():
    responses = [
        httpx.Response(503),
        httpx.Response(503),
        httpx.Response(200, json={"data": 1}),
    ]
    client, clock, _ = _client(lambda r: responses.pop(0))
    assert await client.get_json(URL) == 1
    assert clock.sleeps == [2.0, 4.0]
    await client.aclose()


async def test_404_is_not_retried():
    client, _, seen = _client(lambda r: httpx.Response(404, json={"error": "not found"}))
    with pytest.raises(ApiError):
        await client.get_json(URL)
    assert len(seen) == 1
    await client.aclose()


async def test_connection_errors_are_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"data": "ok"})

    client, _, _ = _client(handler)
    assert await client.get_json(URL) == "ok"
    await client.aclose()


async def test_conditional_request_sends_the_stored_etag_and_reuses_the_body_on_304(conn):
    cache = HttpCacheRepo(conn)
    responses = [
        httpx.Response(200, json={"data": {"v": 1}}, headers={"ETag": 'W/"abc"'}),
        httpx.Response(304),
    ]
    client, _, seen = _client(lambda r: responses.pop(0), cache=cache)
    first = await client.get_json(URL, use_cache=True)
    second = await client.get_json(URL, use_cache=True)
    assert first == second == {"v": 1}
    assert seen[1].headers["if-none-match"] == 'W/"abc"'
    await client.aclose()


async def test_the_breaker_blocks_new_requests_while_open():
    client, _, seen = _client(lambda r: httpx.Response(429))
    with pytest.raises(CircuitOpen):
        await client.get_json(URL)
    before = len(seen)
    with pytest.raises(CircuitOpen):
        await client.get_json(URL)
    assert len(seen) == before
    await client.aclose()


async def test_platform_params_are_attached():
    client, _, seen = _client(lambda r: httpx.Response(200, json={"data": {}}))
    await client.get_json(URL)
    query = dict(httpx.URL(str(seen[0].url)).params)
    assert query["platform"] == "pc"
    assert query["crossplay"] == "true"
    assert query["language"] == "en"
    await client.aclose()


async def test_interactive_priority_is_passed_through_to_the_budget():
    client, _, _ = _client(lambda r: httpx.Response(200, json={"data": {}}))
    await client.get_json(URL, priority=Priority.INTERACTIVE)
    assert client.budget.spent(Priority.INTERACTIVE) == 1
    await client.aclose()


async def test_a_429_backoff_gates_every_caller_not_just_the_one_that_got_it():
    """A 429 is about the client's whole IP, so the hold is shared.

    Concurrency itself is not observable here: the fake clock is global, so the
    backing-off task advances time for every other task before it yields. This
    asserts the gate directly. Phase 7 needs a fake that schedules wakeups per task,
    and that is where the ordering becomes testable.
    """
    client, clock, _ = _client(lambda r: httpx.Response(429, headers={"Retry-After": "30"}))
    with pytest.raises(CircuitOpen):
        await client.get_json(URL)
    assert client.holding_until == pytest.approx(60.0)

    clock.advance(-30)  # a fresh caller arriving mid-hold
    await client._await_hold()
    assert clock.now() == pytest.approx(60.0)
    await client.aclose()


async def test_the_breaker_is_rechecked_after_the_budget_wait():
    """The guards are cheap; the wait between them is not. A request that passed the
    breaker before queueing must not be released into a block that opened meanwhile."""
    client, _, seen = _client(lambda r: httpx.Response(200, json={"data": {}}))
    client._breaker.record_429()
    client._breaker.record_429()

    original = client.budget.acquire

    async def trip_while_waiting(priority=Priority.BACKGROUND):
        await original(priority)
        client._breaker.record_429()  # the block opens while this caller was queued

    client.budget.acquire = trip_while_waiting
    with pytest.raises(CircuitOpen):
        await client.get_json(URL)
    assert seen == []
    await client.aclose()


async def test_a_hold_extended_during_the_wait_is_honoured():
    client, clock, seen = _client(lambda r: httpx.Response(200, json={"data": {}}))
    original = client.budget.acquire

    async def extend_while_waiting(priority=Priority.BACKGROUND):
        await original(priority)
        client._hold_until = clock.now() + 50

    client.budget.acquire = extend_while_waiting
    await client.get_json(URL)
    assert clock.now() >= 50
    await client.aclose()


async def test_repeated_403_trips_the_breaker():
    client, _, seen = _client(lambda r: httpx.Response(403, json={"error": "forbidden"}))
    for _ in range(3):
        with pytest.raises(ApiError):
            await client.get_json(URL)
    assert len(seen) == 3
    with pytest.raises(CircuitOpen):
        await client.get_json(URL)
    assert len(seen) == 3
    await client.aclose()


async def test_a_redirect_is_an_error_not_an_empty_payload():
    client, _, _ = _client(
        lambda r: httpx.Response(302, headers={"Location": "https://example.invalid/"})
    )
    with pytest.raises(ApiError):
        await client.get_json(URL)
    await client.aclose()


async def test_a_redirect_does_not_poison_the_cache(conn):
    cache = HttpCacheRepo(conn)
    client, _, _ = _client(
        lambda r: httpx.Response(301, headers={"Location": "https://example.invalid/"}),
        cache=cache,
    )
    with pytest.raises(ApiError):
        await client.get_json(URL, use_cache=True)
    assert cache.get(str(httpx.URL(URL).copy_merge_params({
        "platform": "pc", "language": "en", "crossplay": "true"}))) is None
    await client.aclose()


async def test_a_304_without_a_cached_body_is_an_error():
    client, _, _ = _client(lambda r: httpx.Response(304))
    with pytest.raises(ApiError):
        await client.get_json(URL)
    await client.aclose()


async def test_persistent_connection_errors_raise_apierror_and_feed_the_breaker():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client, _, seen = _client(handler)
    with pytest.raises(ApiError):
        await client.get_json(URL)
    assert len(seen) == 5
    assert client._breaker.is_open is True
    await client.aclose()


async def test_a_bare_error_envelope_is_raised_not_returned():
    client, _, _ = _client(lambda r: httpx.Response(200, json={"error": "item not found"}))
    with pytest.raises(ApiError):
        await client.get_json(URL)
    await client.aclose()


async def test_no_sleep_is_served_after_the_final_attempt():
    client, clock, seen = _client(lambda r: httpx.Response(503))
    with pytest.raises(ApiError):
        await client.get_json(URL)
    assert len(seen) == 5
    assert len(clock.sleeps) == 4
    await client.aclose()
