from __future__ import annotations

from typing import Protocol, runtime_checkable

from wfm.news.types import Article, NewsSource


@runtime_checkable
class Source(Protocol):
    """One upstream feed.

    A source's only job is to return Articles. It does no matching, no classification and
    no persistence, so adding a feed later is one file and one config entry.
    """

    name: NewsSource

    async def fetch(self) -> list[Article]: ...
