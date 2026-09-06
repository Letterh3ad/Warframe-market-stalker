"""Pure fuzzy gate: which catalog items does this article text mention?

This module imports only stdlib and wfm.models. It is the cost gate for the whole news
pipeline: most articles mention no tradeable item, and dropping those here costs nothing,
so only survivors ever reach a classifier.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from wfm.models import Item

# Matched against the ORIGINAL text, not text.lower(). Lowercasing can change string
# length for some Unicode, which would make every offset below index the wrong string.
_WORD = re.compile(r"[A-Za-z0-9]+")

# Only "set" is stripped. Part suffixes (blueprint, neuroptics, barrel, ...) are left
# alone deliberately: an article naming a specific part means that part, and turning
# "Mesa Prime Neuroptics" into "Mesa Prime" would lose that.
_SET_SUFFIX = "set"


def normalize(text: str) -> list[tuple[str, int]]:
    """Lowercased word tokens paired with their start offset in the ORIGINAL text.

    Offsets are kept because the matcher needs them twice: to check capitalisation of a
    single-token match in the source casing, and to slice context out for the classifier.
    Both index the original string, so the scan runs over `text` and lowercases each
    token afterwards rather than lowercasing the whole string first.
    """
    return [(m.group(0).lower(), m.start()) for m in _WORD.finditer(text)]


@dataclass(frozen=True)
class Lexicon:
    """Item names indexed by their first token, each bucket sorted longest-first.

    Longest-first is what makes the matcher's first hit the right one: at "mesa prime
    set" the three-token entry is tried before the two-token alias, so the gate reports
    one candidate rather than two overlapping ones.
    """

    by_first: dict[str, tuple[tuple[tuple[str, ...], str, str], ...]]
    max_len: int


def build_lexicon(items: Iterable[Item]) -> Lexicon:
    entries: dict[tuple[str, ...], tuple[str, str]] = {}
    items = list(items)

    for item in items:
        tokens = tuple(t for t, _ in normalize(item.name))
        if tokens:
            entries.setdefault(tokens, (item.slug, item.name))

    # Aliases run in a second pass so a real item name always beats an alias that
    # happens to collide with it.
    for item in items:
        if not item.is_set:
            continue
        tokens = tuple(t for t, _ in normalize(item.name))
        if len(tokens) > 1 and tokens[-1] == _SET_SUFFIX:
            entries.setdefault(tokens[:-1], (item.slug, item.name))

    buckets: dict[str, list[tuple[tuple[str, ...], str, str]]] = {}
    for tokens, (slug, name) in entries.items():
        buckets.setdefault(tokens[0], []).append((tokens, slug, name))

    by_first = {
        first: tuple(sorted(rows, key=lambda row: (-len(row[0]), row[0])))
        for first, rows in buckets.items()
    }
    max_len = max((len(t) for t in entries), default=0)
    return Lexicon(by_first=by_first, max_len=max_len)
