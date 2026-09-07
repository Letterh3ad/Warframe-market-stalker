"""r/Warframe, over Atom.

The spec called for reddit's public JSON listings. They answer 403 to every
User-Agent, browser strings included, so the Atom feed is the endpoint that works. It
carries the same selftext. Reddit also rate limits hard, answering 429 to a repeat
request minutes later, which is why this source is off by default in config and why the
fetcher honours Retry-After.
"""

from __future__ import annotations

from wfm.news.fetch import NewsFetcher
from wfm.news.html import strip_tags
from wfm.news.sources.feed import parse_atom
from wfm.news.types import Article, NewsSource

FEED_URL = "https://www.reddit.com/r/Warframe/hot.rss?limit=25"


class RedditSource:
    name = NewsSource.REDDIT

    def __init__(self, fetcher: NewsFetcher, url: str = FEED_URL) -> None:
        self._fetcher = fetcher
        self._url = url

    async def fetch(self) -> list[Article]:
        xml = await self._fetcher.get_text(self._url)
        return [
            Article(
                source=NewsSource.REDDIT,
                external_id=f"reddit:{entry.entry_id}",
                url=entry.url,
                title=entry.title,
                # ElementTree already unescaped the entity-encoded HTML once when it
                # read <content>, so what arrives here is real markup. Unescaping again
                # would start interpreting the post's own literal &lt; as tags.
                body=strip_tags(entry.body_html),
                published_at=entry.published,
            ).hashed()
            for entry in parse_atom(xml)
        ]
