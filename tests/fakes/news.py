from __future__ import annotations

from wfm.news.types import Article, NewsSource


class FakeSource:
    """A Source that yields canned articles, so the suite never touches the network.

    `fail_with` simulates an upstream outage, which the ingest loop must survive without
    losing the sources that did succeed.
    """

    def __init__(
        self,
        articles: list[Article],
        name: NewsSource = NewsSource.WARFRAME_NEWS,
        fail_with: Exception | None = None,
    ) -> None:
        self.name = name
        self._articles = articles
        self._fail_with = fail_with
        self.calls = 0

    async def fetch(self) -> list[Article]:
        self.calls += 1
        if self._fail_with is not None:
            raise self._fail_with
        return list(self._articles)
