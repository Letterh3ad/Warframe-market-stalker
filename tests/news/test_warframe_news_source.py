import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from tests.fakes.clock import FakeClock
from wfm.news.fetch import NewsFetcher
from wfm.news.sources.base import Source
from wfm.news.sources.warframe_news import LISTING_URL, WarframeNewsSource
from wfm.news.types import NewsSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "news"
LISTING = (FIXTURES / "warframe_posts.json").read_text(encoding="utf-8")
ARTICLE = (FIXTURES / "warframe_article.html").read_text(encoding="utf-8")


def handler(request):
    if "search_posts_json" in str(request.url):
        return httpx.Response(200, text=LISTING)
    return httpx.Response(200, text=ARTICLE)


def source(h=handler, **kwargs) -> tuple[WarframeNewsSource, NewsFetcher]:
    fetcher = NewsFetcher(
        clock=FakeClock(datetime(2026, 9, 6, tzinfo=timezone.utc)),
        user_agent="WFMStalker/test",
        transport=httpx.MockTransport(h),
    )
    return WarframeNewsSource(fetcher, **kwargs), fetcher


async def test_it_satisfies_the_source_protocol():
    src, fetcher = source()
    try:
        assert isinstance(src, Source)
        assert src.name is NewsSource.WARFRAME_NEWS
    finally:
        await fetcher.aclose()


def test_the_listing_is_the_json_endpoint():
    assert "search_posts_json" in LISTING_URL


async def test_it_maps_the_real_listing_to_articles():
    src, fetcher = source(max_bodies=2)
    try:
        articles = await src.fetch()
    finally:
        await fetcher.aclose()

    assert len(articles) == 2
    first = articles[0]
    assert first.source is NewsSource.WARFRAME_NEWS
    assert first.external_id == "warframe_news:citrine-prime-access"
    assert first.title == "Citrine Prime Access"
    assert first.url == "https://www.warframe.com/en/news/citrine-prime-access"


async def test_the_publish_date_is_read_as_toronto_local_time():
    src, fetcher = source(max_bodies=1)
    try:
        (article,) = await src.fetch()
    finally:
        await fetcher.aclose()

    # Listing says 2026-09-04 07:54:00 with no offset; Toronto is UTC-4 in September.
    assert article.published_at == datetime(2026, 9, 4, 11, 54, tzinfo=timezone.utc)


async def test_the_body_comes_from_the_article_page_not_the_teaser():
    src, fetcher = source(max_bodies=1)
    try:
        (article,) = await src.fetch()
    finally:
        await fetcher.aclose()

    assert "Shine on with a diamond" in article.body
    assert "Corufell Prime" in article.body
    assert article.body != "Wake up flawless on September 23."


async def test_it_spends_one_request_on_the_listing_plus_one_per_body():
    calls = []

    def counting(request):
        calls.append(str(request.url))
        return handler(request)

    src, fetcher = source(counting, max_bodies=3)
    try:
        await src.fetch()
    finally:
        await fetcher.aclose()

    assert len(calls) == 4
    assert "search_posts_json" in calls[0]


async def test_known_articles_are_skipped_entirely():
    calls = []

    def counting(request):
        calls.append(str(request.url))
        return handler(request)

    known = {"warframe_news:citrine-prime-access"}
    src, fetcher = source(counting, known_ids=lambda: known, max_bodies=1)
    try:
        articles = await src.fetch()
    finally:
        await fetcher.aclose()

    ids = [a.external_id for a in articles]
    assert "warframe_news:citrine-prime-access" not in ids
    # Skipped means not re-fetched: only the listing plus the one article that ran.
    assert len(calls) == 2


async def test_known_ids_are_read_at_fetch_time_not_at_construction():
    known: set[str] = set()
    src, fetcher = source(max_bodies=1)
    try:
        src = WarframeNewsSource(fetcher, known_ids=lambda: known, max_bodies=1)
        known.add("warframe_news:citrine-prime-access")
        (article,) = await src.fetch()
    finally:
        await fetcher.aclose()
    assert article.external_id != "warframe_news:citrine-prime-access"


async def test_the_body_budget_caps_the_fan_out():
    src, fetcher = source(max_bodies=1)
    try:
        articles = await src.fetch()
    finally:
        await fetcher.aclose()
    # The listing holds ten posts; the rest wait for the next poll rather than
    # arriving with an empty body that would hash as an edit later.
    assert len(articles) == 1


async def test_an_article_whose_body_cannot_be_found_still_yields_no_partial_hash():
    def bodyless(request):
        if "search_posts_json" in str(request.url):
            return httpx.Response(200, text=LISTING)
        return httpx.Response(200, text="<html><body>no post body here</body></html>")

    src, fetcher = source(bodyless, max_bodies=1)
    try:
        (article,) = await src.fetch()
    finally:
        await fetcher.aclose()
    assert article.body == ""
    assert article.content_hash


async def test_a_listing_with_an_unparseable_date_still_yields_the_article():
    posts = json.loads(LISTING)
    posts["posts"][0]["date"] = "not a date"

    def broken(request):
        if "search_posts_json" in str(request.url):
            return httpx.Response(200, text=json.dumps(posts))
        return httpx.Response(200, text=ARTICLE)

    src, fetcher = source(broken, max_bodies=1)
    try:
        (article,) = await src.fetch()
    finally:
        await fetcher.aclose()
    assert article.published_at is None
