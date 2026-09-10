"""date_text (as the article wrote it) plus the publish date -> effective_at.

The model reports the date as written and never computes one, because relative date
arithmetic is where small models fail hardest and effective_at anchors the decay
curve. This module parses only shapes that are unambiguous and answers None for
everything else: a missing effective_at degrades to published_at, which the bias
query already handles, while a wrong one silently misprices an item.
"""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone

# A date landing more than this far before publication reads as next year's, not as
# an unexplained backdate. Hotfixes do describe the recent past; nothing describes
# last December in an article published this September.
_BACKDATE_GRACE = timedelta(days=30)

_MONTHS = {
    name.lower(): number for number, name in enumerate(calendar.month_name) if name
} | {name.lower(): number for number, name in enumerate(calendar.month_abbr) if name} | {
    'sept': 9,
}

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_FIRST = re.compile(
    r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b"
)
_DAY_FIRST = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Za-z]{3,9})\.?(?:,?\s+(\d{4}))?\b"
)


def resolve_effective_at(
    timing: str, date_text: str | None, published_at: datetime | None
) -> datetime | None:
    if timing == "immediate":
        return published_at
    if timing != "dated" or not date_text:
        return None
    return _parse(date_text, published_at)


def _parse(text: str, published_at: datetime | None) -> datetime | None:
    iso = _ISO.search(text)
    if iso is not None:
        return _build(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))

    month_first = _MONTH_FIRST.search(text)
    if month_first is not None:
        month = _MONTHS.get(month_first.group(1).lower())
        if month is not None:
            return _with_year(
                month, int(month_first.group(2)), month_first.group(3), published_at
            )

    day_first = _DAY_FIRST.search(text)
    if day_first is not None:
        month = _MONTHS.get(day_first.group(2).lower())
        if month is not None:
            return _with_year(
                month, int(day_first.group(1)), day_first.group(3), published_at
            )

    return None


def _with_year(
    month: int, day: int, year_text: str | None, published_at: datetime | None
) -> datetime | None:
    if year_text is not None:
        return _build(int(year_text), month, day)
    if published_at is None:
        return None

    candidate = _build(published_at.year, month, day)
    if candidate is None:
        return None
    if candidate < published_at - _BACKDATE_GRACE:
        return _build(published_at.year + 1, month, day)
    return candidate


def _build(year: int, month: int, day: int) -> datetime | None:
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None
