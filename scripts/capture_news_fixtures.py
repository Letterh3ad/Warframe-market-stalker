"""Capture live news payloads into tests/fixtures/news for parser development.

Run by hand:  python scripts/capture_news_fixtures.py
Never invoked by the test suite. Costs five requests to three unrelated hosts.

These are NOT warframe.market, so this deliberately does not use the market client or
its TokenBucket: that budget exists to keep the tool compliant with warframe.market's
published rate limit, and spending it on unrelated hosts would corrupt the accounting.

Endpoint choices are reconnaissance results, not preferences (see the "Source
reconnaissance 2026-09-06" section of the design doc):

- forums.warframe.com serves 403 to every non-browser client on its HTML, but its
  Invision RSS (`<forum path>.xml/`, trailing slash required) returns 200 with the full
  post body inline.
- reddit.com/*.json serves 403 regardless of User-Agent; the Atom feed returns 200 and
  carries the same selftext.
- warframe.com's listing is backed by a paginated JSON endpoint (`search_posts_json`);
  the HTML is captured anyway as the fallback parser's fixture. Both carry a teaser
  only, so one article page is captured too.
- reddit's Atom feed answers 429 if hit again within a few minutes.

Feeds are trimmed to FEED_ITEMS entries: the forums feed is 874KB whole, and a parser
fixture needs shape, not volume.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx

OUT = Path("tests/fixtures/news")
UA = "WFMStalker/0.1 (+https://github.com/Faye/Warframe-market-stalker)"
FEED_ITEMS = 3

TARGETS = {
    "warframe_posts.json": (
        "https://www.warframe.com/en/news/search_posts_json?query=&page=1&version=2"
    ),
    "warframe_news.html": "https://www.warframe.com/news",
    "warframe_article.html": "https://www.warframe.com/en/news/citrine-prime-access",
    "forums_updates.xml": "https://forums.warframe.com/forum/3-pc-update-notes.xml/",
    "reddit_hot.xml": "https://www.reddit.com/r/Warframe/hot.rss?limit=25",
}


def trim_feed(text: str, tag: str, keep: int = FEED_ITEMS) -> str:
    """Drop all but the first `keep` `<tag>` elements, leaving the envelope intact."""
    matches = list(re.finditer(rf"<{tag}>.*?</{tag}>", text, re.S))
    if len(matches) <= keep:
        return text
    return text[: matches[keep].start()] + text[matches[-1].end() :]


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(
        headers={"User-Agent": UA}, timeout=20.0, follow_redirects=True
    ) as client:
        for name, url in TARGETS.items():
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                print(f"{name}: FAILED {exc!r}")
                continue
            if response.status_code != 200:
                print(f"{name}: HTTP {response.status_code}, not written")
                continue
            body = response.text
            # Feeds are trimmed before the cap: a mid-element cut leaves unclosed CDATA.
            if name.endswith(".xml"):
                body = trim_feed(body, "item" if "<item>" in body else "entry")
            body = body[:400_000]
            (OUT / name).write_text(body, encoding="utf-8")
            print(f"{name}: HTTP 200, {len(response.text)} chars, wrote {len(body)}")


if __name__ == "__main__":
    asyncio.run(main())
