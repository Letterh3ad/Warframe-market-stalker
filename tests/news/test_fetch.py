from datetime import datetime, timezone

import httpx
import pytest

from tests.fakes.clock import FakeClock
from wfm.news.fetch import NewsFetcher, NewsFetchError

START = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def fetcher(handler, **kwargs) -> NewsFetcher:
    return NewsFetcher(
        clock=kwargs.pop("clock", FakeClock(START)),
        user_agent="WFMStalker/test",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


async def test_get_text_returns_the_body():
    f = fetcher(lambda request: httpx.Response(200, text="hello"))
    try:
        assert await f.get_text("https://example.test/a") == "hello"
    finally:
        await f.aclose()


async def test_the_user_agent_is_sent():
    seen = []

    def handler(request):
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, text="ok")

    f = fetcher(handler)
    try:
        await f.get_text("https://example.test/a")
    finally:
        await f.aclose()
    assert seen == ["WFMStalker/test"]


async def test_requests_to_one_host_are_paced():
    clock = FakeClock(START)
    f = fetcher(lambda request: httpx.Response(200, text="ok"), clock=clock, min_interval_s=2.0)
    try:
        await f.get_text("https://example.test/a")
        await f.get_text("https://example.test/b")
    finally:
        await f.aclose()
    assert clock.sleeps == [2.0]


async def test_pacing_is_per_host_so_one_upstream_does_not_slow_another():
    clock = FakeClock(START)
    f = fetcher(lambda request: httpx.Response(200, text="ok"), clock=clock, min_interval_s=2.0)
    try:
        await f.get_text("https://one.test/a")
        await f.get_text("https://two.test/a")
    finally:
        await f.aclose()
    assert clock.sleeps == []


async def test_a_429_is_retried_after_the_retry_after_delay():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, text="slow down")
        return httpx.Response(200, text="ok")

    clock = FakeClock(START)
    f = fetcher(handler, clock=clock)
    try:
        assert await f.get_text("https://example.test/a") == "ok"
    finally:
        await f.aclose()
    assert 7.0 in clock.sleeps


async def test_a_huge_retry_after_makes_the_fetcher_give_up_without_sleeping_it_out():
    clock = FakeClock(START)

    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "3600"}, text="come back later")

    f = fetcher(handler, clock=clock)
    try:
        with pytest.raises(NewsFetchError, match="429|3600"):
            await f.get_text("https://example.test/a")
    finally:
        await f.aclose()
    assert 3600 not in clock.sleeps
    assert max(clock.sleeps, default=0) <= 60


async def test_a_persistent_429_gives_up_with_the_status_in_the_message():
    f = fetcher(lambda request: httpx.Response(429, text="no"), max_attempts=2)
    try:
        with pytest.raises(NewsFetchError, match="429"):
            await f.get_text("https://example.test/a")
    finally:
        await f.aclose()


async def test_a_500_is_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500 if len(calls) == 1 else 200, text="ok")

    f = fetcher(handler)
    try:
        assert await f.get_text("https://example.test/a") == "ok"
    finally:
        await f.aclose()
    assert len(calls) == 2


async def test_a_403_fails_immediately_and_says_to_use_the_feed():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(403, text="blocked")

    f = fetcher(handler)
    try:
        with pytest.raises(NewsFetchError, match="feed"):
            await f.get_text("https://example.test/a")
    finally:
        await f.aclose()
    # Retrying a block just spends requests on a host that has already said no.
    assert len(calls) == 1


async def test_a_404_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404, text="gone")

    f = fetcher(handler)
    try:
        with pytest.raises(NewsFetchError, match="404"):
            await f.get_text("https://example.test/a")
    finally:
        await f.aclose()
    assert len(calls) == 1


async def test_a_transport_error_is_retried_then_raised_as_a_news_error():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    f = fetcher(handler, max_attempts=2)
    try:
        with pytest.raises(NewsFetchError, match="connection failed"):
            await f.get_text("https://example.test/a")
    finally:
        await f.aclose()


async def test_redirects_are_followed():
    def handler(request):
        if request.url.path == "/news":
            return httpx.Response(302, headers={"Location": "https://example.test/en/news"})
        return httpx.Response(200, text="landed")

    f = fetcher(handler)
    try:
        assert await f.get_text("https://example.test/news") == "landed"
    finally:
        await f.aclose()
