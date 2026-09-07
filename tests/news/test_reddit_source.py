from datetime import datetime, timezone
from pathlib import Path

import httpx

from tests.fakes.clock import FakeClock
from wfm.news.fetch import NewsFetcher
from wfm.news.sources.base import Source
from wfm.news.sources.reddit import FEED_URL, RedditSource
from wfm.news.types import NewsSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "news"
FEED = (FIXTURES / "reddit_hot.xml").read_text(encoding="utf-8")


def source(handler) -> tuple[RedditSource, NewsFetcher]:
    fetcher = NewsFetcher(
        clock=FakeClock(datetime(2026, 9, 6, tzinfo=timezone.utc)),
        user_agent="WFMStalker/test",
        transport=httpx.MockTransport(handler),
    )
    return RedditSource(fetcher), fetcher


async def test_it_satisfies_the_source_protocol():
    src, fetcher = source(lambda request: httpx.Response(200, text=FEED))
    try:
        assert isinstance(src, Source)
        assert src.name is NewsSource.REDDIT
    finally:
        await fetcher.aclose()


def test_the_feed_url_is_the_atom_feed_not_the_json_listing():
    # .json answers 403 to every User-Agent. Measured, not assumed.
    assert ".rss" in FEED_URL
    assert ".json" not in FEED_URL


async def test_it_maps_the_real_feed_to_articles():
    src, fetcher = source(lambda request: httpx.Response(200, text=FEED))
    try:
        articles = await src.fetch()
    finally:
        await fetcher.aclose()

    assert len(articles) == 3
    first = articles[0]
    assert first.source is NewsSource.REDDIT
    assert first.external_id == "reddit:t3_1vy9lck"
    assert first.url.startswith("https://www.reddit.com/r/Warframe/comments/")
    assert first.published_at == datetime(2026, 8, 25, 19, 20, 27, tzinfo=timezone.utc)


async def test_the_selftext_is_flattened_to_plain_text():
    src, fetcher = source(lambda request: httpx.Response(200, text=FEED))
    try:
        articles = await src.fetch()
    finally:
        await fetcher.aclose()

    body = articles[0].body
    assert "Riven" in body
    assert "<p>" not in body
    assert "&lt;" not in body


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
