"""RSS 2.0 and Atom readers, shared by the forums and reddit sources.

Both feeds are third-party XML. ElementTree does not expand external entities and
raises on undefined ones, so the classic entity-expansion attacks fail closed here;
a malformed feed surfaces as ParseError, which the ingest loop treats as a source
failure like any other.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass(frozen=True)
class FeedEntry:
    entry_id: str
    url: str
    title: str
    body_html: str
    published: datetime | None = None


def parse_rss(xml: str) -> list[FeedEntry]:
    root = ET.fromstring(xml)
    entries = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        # guid first: the link's slug half changes when a topic is retitled, the id
        # does not, and external_id has to survive a retitle.
        entry_id = (item.findtext("guid") or "").strip() or link
        title = (item.findtext("title") or "").strip()
        if not entry_id or not title:
            continue
        entries.append(
            FeedEntry(
                entry_id=entry_id,
                url=link,
                title=title,
                body_html=item.findtext("description") or "",
                published=_rfc2822(item.findtext("pubDate")),
            )
        )
    return entries


def parse_atom(xml: str) -> list[FeedEntry]:
    root = ET.fromstring(xml)
    entries = []
    for entry in root.iter(ATOM + "entry"):
        entry_id = (entry.findtext(ATOM + "id") or "").strip()
        title = (entry.findtext(ATOM + "title") or "").strip()
        if not entry_id or not title:
            continue
        link = entry.find(ATOM + "link")
        entries.append(
            FeedEntry(
                entry_id=entry_id,
                url=(link.get("href") or "") if link is not None else "",
                title=title,
                body_html=entry.findtext(ATOM + "content") or "",
                published=_iso(entry.findtext(ATOM + "published")),
            )
        )
    return entries


def _rfc2822(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return _aware(parsedate_to_datetime(raw.strip()))
    except (TypeError, ValueError):
        return None


def _iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return _aware(datetime.fromisoformat(raw.strip()))
    except ValueError:
        return None


def _aware(value: datetime) -> datetime | None:
    """None for a naive datetime rather than a guessed timezone.

    to_utc_iso refuses naive input for exactly this reason, and an article with no
    publish date still stores and sorts by fetched_at. Both real feeds carry an
    explicit offset, so this is a guard rather than the normal path.
    """
    return value if value.tzinfo is not None else None
