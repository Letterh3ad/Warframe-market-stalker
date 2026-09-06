"""Official forum update and hotfix notes, over Invision's RSS.

There is no HTML path here on purpose: forums.warframe.com answers 403 to every
non-browser client, with a browser User-Agent too. The feed is the only way in, and it
carries the whole post body inline, so a poll is exactly one request.
"""

from __future__ import annotations

from wfm.news.fetch import NewsFetcher
from wfm.news.html import strip_tags
from wfm.news.sources.feed import parse_rss
from wfm.news.types import Article, NewsSource

# The trailing slash is required. Without it the host answers 403.
FEED_URL = "https://forums.warframe.com/forum/3-pc-update-notes.xml/"


class ForumsSource:
    name = NewsSource.FORUMS

    def __init__(self, fetcher: NewsFetcher, url: str = FEED_URL) -> None:
        self._fetcher = fetcher
        self._url = url

    async def fetch(self) -> list[Article]:
        xml = await self._fetcher.get_text(self._url)
        return [
            Article(
                source=NewsSource.FORUMS,
                # Namespaced because news_articles.external_id is globally unique, not
                # unique per source: a bare topic id could collide with another feed's.
                external_id=f"forums:{entry.entry_id}",
                url=entry.url,
                title=entry.title,
                body=strip_tags(entry.body_html),
                published_at=entry.published,
            ).hashed()
            for entry in parse_rss(xml)
        ]
