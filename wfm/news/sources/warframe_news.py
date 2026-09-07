"""warframe.com/news, over the JSON endpoint its own load-more button calls.

The listing page is server-rendered and parseable, but it also carries an eleventh
NewsCard inside a handlebars <script> template whose date is the literal "{{date}}", so
the JSON is both cheaper and safer. It returns {"posts": [...], "hasMore": bool}, ten
posts a page, each {date, title, description, url, image}.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from wfm.news.fetch import NewsFetcher
from wfm.news.html import extract_element_text
from wfm.news.types import Article, NewsSource

LISTING_URL = "https://www.warframe.com/en/news/search_posts_json?query=&page=1&version=2"
BODY_ELEMENT_ID = "post-body"

# Established, not assumed: the Citrine Prime Access post is stamped 07:54:00 and the
# first r/Warframe reactions to it are 11:56:53, 12:00:13 and 12:00:21 UTC, which puts
# the announcement two minutes before the first reaction at UTC-4. DE is in London,
# Ontario. A zone rather than a fixed offset because the corpus crosses DST.
PUBLISH_TZ = ZoneInfo("America/Toronto")
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class WarframeNewsSource:
    name = NewsSource.WARFRAME_NEWS

    def __init__(
        self,
        fetcher: NewsFetcher,
        known_ids: Callable[[], set[str]] | None = None,
        max_bodies: int = 10,
        listing_url: str = LISTING_URL,
    ) -> None:
        self._fetcher = fetcher
        # A callable, not a set: the daemon builds its sources once and polls for days,
        # so a snapshot taken at construction would re-fetch every body forever.
        self._known_ids = known_ids or (lambda: set())
        self._max_bodies = max_bodies
        self._listing_url = listing_url

    async def fetch(self) -> list[Article]:
        payload = json.loads(await self._fetcher.get_text(self._listing_url))
        known = self._known_ids()
        budget = self._max_bodies
        articles: list[Article] = []

        for post in payload.get("posts") or []:
            url = (post.get("url") or "").strip()
            title = (post.get("title") or "").strip()
            if not url or not title:
                continue
            external_id = f"warframe_news:{_slug(url)}"

            # Skipped entirely rather than emitted with the teaser as its body: the
            # content hash covers title plus body, so a teaser-bodied re-emit would
            # look like an edit, wiping the stored events and re-gating the teaser.
            # The cost is that edits to a warframe.com article go undetected, which is
            # accepted: the source that DE actually edits is the forums, and its
            # bodies are free.
            if external_id in known:
                continue
            if budget <= 0:
                break
            budget -= 1

            page = await self._fetcher.get_text(url)
            body = extract_element_text(page, BODY_ELEMENT_ID)
            # An empty body means the post-body element was absent: a renamed element, an
            # interstitial, or a JS-only render. Storing it now would freeze it bodyless
            # forever, since its id lands in known_ids and later polls skip it. Drop it
            # instead so the next poll retries; the body request is not refunded to
            # budget, so a run of bodyless pages cannot fan out unboundedly.
            if not body.strip():
                continue
            articles.append(
                Article(
                    source=NewsSource.WARFRAME_NEWS,
                    external_id=external_id,
                    url=url,
                    title=title,
                    body=body,
                    published_at=_published(post.get("date")),
                ).hashed()
            )
        return articles


def _slug(url: str) -> str:
    """The last path segment, which is what stably identifies a post.

    The payload carries no numeric id, and the path is locale-prefixed
    (/en/news/<slug>), so the segment is the identifier and the locale is not part of it.
    """
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def _published(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), _DATE_FORMAT).replace(tzinfo=PUBLISH_TZ)
    except ValueError:
        return None
