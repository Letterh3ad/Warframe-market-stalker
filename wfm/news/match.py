"""Pure fuzzy gate: which catalog items does this article text mention?

This module imports only stdlib and wfm.models. It is the cost gate for the whole news
pipeline: most articles mention no tradeable item, and dropping those here costs nothing,
so only survivors ever reach a classifier.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

from wfm.models import Item
from wfm.news.types import Candidate

# Matched against the ORIGINAL text, not text.lower(). Lowercasing can change string
# length for some Unicode, which would make every offset below index the wrong string.
_WORD = re.compile(r"[A-Za-z0-9]+")

# Only "set" is stripped. Part suffixes (blueprint, neuroptics, barrel, ...) are left
# alone deliberately: an article naming a specific part means that part, and turning
# "Mesa Prime Neuroptics" into "Mesa Prime" would lose that.
_SET_SUFFIX = "set"

# Single-word catalog names that are also ordinary English. Derived from the catalog once
# (SELECT name FROM items WHERE LENGTH(name)<=6 AND name NOT LIKE '% %'), not guessed.
# These match only mid-sentence, where a capital is a real proper-noun signal. Requiem
# mods (Xata, Vome, Fass, Khra, ...) are deliberately absent: they are distinctive and
# should match anywhere, which is why name length is the wrong discriminator.
_AMBIGUOUS_SINGLE_TOKENS = frozenset(
    {
        "bite", "bore", "dig", "flow", "fury", "howl", "hunt", "hush",
        "jolt", "maim", "maul", "rage", "rush",
    }
)

_SENTENCE_END = ".!?"


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


def find_candidates(
    text: str,
    lexicon: Lexicon,
    threshold: float = 0.88,
    context_chars: int = 200,
) -> list[Candidate]:
    """Catalog items mentioned in `text`, best score per slug, highest score first.

    Scans left to right. At each position the longest lexicon entry starting with that
    token is tried first, and a hit consumes its tokens, so overlapping matches cannot
    both be reported.
    """
    tokens = normalize(text)
    best: dict[str, Candidate] = {}
    i = 0

    while i < len(tokens):
        hit = _match_at(text, tokens, i, lexicon, threshold)
        if hit is None:
            i += 1
            continue

        span_len, slug, name, score, start, end = hit
        previous = best.get(slug)
        if previous is None or score > previous.score:
            best[slug] = Candidate(
                slug=slug,
                name=name,
                score=score,
                context=_context(text, start, end, context_chars),
                start=start,
                end=end,
            )
        i += span_len

    return sorted(best.values(), key=lambda c: (-c.score, c.slug))


def _match_at(
    text: str,
    tokens: list[tuple[str, int]],
    i: int,
    lexicon: Lexicon,
    threshold: float,
) -> tuple[int, str, str, float, int, int] | None:
    """The longest lexicon entry matching at token index `i`, or None."""
    for entry_tokens, slug, name in lexicon.by_first.get(tokens[i][0], ()):
        n = len(entry_tokens)
        if i + n > len(tokens):
            continue

        span = tuple(t for t, _ in tokens[i : i + n])
        if span == entry_tokens:
            score = 1.0
        else:
            score = SequenceMatcher(None, " ".join(span), " ".join(entry_tokens)).ratio()
            if score < threshold:
                continue

        start = tokens[i][1]
        if n == 1:
            token, _ = tokens[i]
            if not text[start].isupper():
                continue
            if token in _AMBIGUOUS_SINGLE_TOKENS and _sentence_initial(text, start):
                continue

        last_token, last_start = tokens[i + n - 1]
        return n, slug, name, score, start, last_start + len(last_token)

    return None


def _sentence_initial(text: str, start: int) -> bool:
    """True when nothing but whitespace separates `start` from a sentence end or the
    start of the text, i.e. the capital there carries no proper-noun information.
    """
    i = start - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    return i < 0 or text[i] in _SENTENCE_END


def _context(text: str, start: int, end: int, context_chars: int) -> str:
    """The match plus surrounding text, clipped to whitespace so words stay whole."""
    left = max(0, start - context_chars)
    right = min(len(text), end + context_chars)
    if left > 0:
        space = text.find(" ", left, start)
        left = space + 1 if space != -1 else left
    if right < len(text):
        space = text.rfind(" ", end, right)
        right = space if space != -1 else right
    return text[left:right].strip()
