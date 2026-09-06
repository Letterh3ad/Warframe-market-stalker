from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from tests.fakes.clock import FakeClock
from wfm.news.fetch import NewsFetcher, NewsFetchError
from wfm.news.sources.base import Source
from wfm.news.sources.forums import FEED_URL, ForumsSource
from wfm.news.types import NewsSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "news"
FEED = (FIXTURES / "forums_updates.xml").read_text(encoding="utf-8")


def source(handler) -> tuple[ForumsSource, NewsFetcher]:
    fetcher = NewsFetcher(
        clock=FakeClock(datetime(2026, 9, 6, tzinfo=timezone.utc)),
        user_agent="WFMStalker/test",
        transport=httpx.MockTransport(handler),
    )
    return ForumsSource(fetcher), fetcher


async def test_it_satisfies_the_source_protocol():
    src, fetcher = source(lambda request: httpx.Response(200, text=FEED))
    try:
        assert isinstance(src, Source)
        assert src.name is NewsSource.FORUMS
    finally:
        await fetcher.aclose()


async def test_the_feed_url_keeps_its_trailing_slash():
    # Without it the host answers 403. This is not cosmetic.
    assert FEED_URL.endswith(".xml/")


async def test_it_maps_the_real_feed_to_articles():
    src, fetcher = source(lambda request: httpx.Response(200, text=FEED))
    try:
        articles = await src.fetch()
    finally:
        await fetcher.aclose()

    assert len(articles) == 3
    first = articles[0]
    assert first.source is NewsSource.FORUMS
    assert first.external_id == "forums:1520640"
    assert first.title.endswith("Hotfix 43.5.3")
    assert first.url.startswith("https://forums.warframe.com/topic/1520640")
    assert first.published_at == datetime(2026, 8, 18, 19, 4, 45, tzinfo=timezone.utc)


async def test_the_body_arrives_inline_as_plain_text():
    src, fetcher = source(lambda request: httpx.Response(200, text=FEED))
    try:
        articles = await src.fetch()
    finally:
        await fetcher.aclose()

    body = articles[0].body
    assert "Nightwave Radio now plays" in body
    assert "<p>" not in body
    assert "&amp;" not in body


async def test_one_request_serves_the_whole_poll():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=FEED)

    src, fetcher = source(handler)
    try:
        await src.fetch()
    finally:
        await fetcher.aclose()
    assert calls == [FEED_URL]


async def test_every_article_carries_a_content_hash():
    src, fetcher = source(lambda request: httpx.Response(200, text=FEED))
    try:
        articles = await src.fetch()
    finally:
        await fetcher.aclose()
    assert all(a.content_hash for a in articles)
    assert len({a.content_hash for a in articles}) == 3


async def test_an_upstream_failure_propagates():
    src, fetcher = source(lambda request: httpx.Response(403, text="blocked"))
    try:
        with pytest.raises(NewsFetchError):
            await src.fetch()
    finally:
        await fetcher.aclose()
