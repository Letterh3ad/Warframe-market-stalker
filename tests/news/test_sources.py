import pytest

from wfm.news.sources.base import Source
from wfm.news.types import Article, NewsSource
from tests.fakes.news import FakeSource

ARTICLE = Article(
    source=NewsSource.WARFRAME_NEWS,
    external_id="a1",
    url="https://example.test/a1",
    title="T",
    body="B",
)


async def test_fake_source_satisfies_the_protocol():
    source = FakeSource([ARTICLE])
    assert isinstance(source, Source)
    assert source.name is NewsSource.WARFRAME_NEWS


async def test_fake_source_returns_its_articles():
    assert await FakeSource([ARTICLE]).fetch() == [ARTICLE]


async def test_fake_source_records_its_calls():
    source = FakeSource([ARTICLE])
    await source.fetch()
    await source.fetch()
    assert source.calls == 2


async def test_fake_source_can_simulate_an_upstream_failure():
    source = FakeSource([ARTICLE], fail_with=RuntimeError("upstream is down"))
    with pytest.raises(RuntimeError, match="upstream is down"):
        await source.fetch()
