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

# A base item named right before the word "prime" is the Prime variant being talked
# about, not the base item. Announcements are the case that matters: on announcement day
# the Prime is not in the catalog at all, so the longest entry that matches is the base
# one, and linking it would attach a Prime Access event to the wrong item.
_PRIME_TOKEN = "prime"

# Single-word catalog names that are also ordinary English. Derived from the catalog once
# (SELECT name FROM items WHERE LENGTH(name)<=6 AND name NOT LIKE '% %'), not guessed.
# These match only mid-sentence, where a capital is a real proper-noun signal. Requiem
# mods (Xata, Vome, Fass, Khra, ...) are deliberately absent: they are distinctive and
# should match anywhere, which is why name length is the wrong discriminator.
_AMBIGUOUS_SINGLE_TOKENS = frozenset(
    {
        "bite", "bore", "dig", "flow", "fury", "howl", "hunt", "hush",
        "jolt", "maim", "maul", "rage", "rush",
        # Frame names that are also ordinary English (decision 2, guard 2). These match
        # only mid-sentence, where a capital carries real proper-noun information.
        #
        # The cost is real and asymmetric: patch notes open lines with the subject
        # ("Ember: fixed ..."), which is sentence-initial and therefore dropped. That is
        # accepted deliberately: "Frost damage", "Volt shields" and "increased Mag
        # capacity" are constant in this prose, and matching them everywhere would put
        # noise into the classifier queue on every single hotfix.
        "ash", "ember", "frost", "mag", "nova", "volt",
        "equinox", "harrow", "limbo", "mirage", "trinity",
    }
)

_SENTENCE_END = ".!?"

# A predicted slug is an exact textual match to an announced name; the uncertainty is
# in whether DE ships that slug, and that is carried by link_method='synthetic' and
# the staleness rule, not by pretending the text matched poorly.
SYNTHETIC_SCORE = 1.0

# Capitalised words that precede "Prime" without naming an item. "Warframe Prime
# Access" is the brand, and Excalibur Prime is founders-only, so its set slug will
# never exist and predicting it would leave a permanently unresolved event.
NEVER_PRIME_BASE = frozenset({"warframe", "prime", "excalibur"})

# Nouns naming a cosmetic or a bundle rather than a tradeable set: "Sphatika Prime
# Syandana" and "Weapons Pack" will never ship a "_set" slug, so synthesizing one for
# them would write a prediction the reconciliation pass can never resolve.
#
# This is maintained data, not a clever heuristic. DE adds new cosmetic slot types
# (Prime Access accessory categories) over time, and each new one will surface here as
# a synthesized slug that never resolves. The fix when that happens is to add the word,
# not to invent a smarter rule.
NEVER_PRIME_FOLLOWED_BY = frozenset(
    {
        "syandana", "decoration", "sigil", "armor", "armour", "ephemera", "noggle",
        "glyph", "sugatra", "skin", "emblem", "captura", "pack", "packs",
        "accessory", "accessories", "bundle", "collection", "earpiece", "oculus",
        "attachment", "attachments", "helmet", "figurine", "diorama", "poster", "scene",
    }
)


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
    # Every slug the catalog actually sells. Synthesis checks this: a Prime released
    # between announcement and ingest must link normally, not be predicted again.
    slugs: frozenset[str] = frozenset()


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

    # Base warframe names. News prose says "Banshee"; the catalog sells "Banshee
    # Prime Set". Derived from the catalog rather than a curated roster, which gets
    # the decision's guard for free: a frame with no Prime has no *_prime_set row, so
    # it registers no alias and cannot link. Warframes only, weapon base names
    # ("Braton") carry far more non-Prime meaning and are a separate decision.
    for item in items:
        if not item.slug.endswith("_prime_set") or "warframe" not in item.tags:
            continue
        tokens = tuple(t for t, _ in normalize(item.name))
        if len(tokens) == 3 and tokens[1] == _PRIME_TOKEN and tokens[2] == _SET_SUFFIX:
            entries.setdefault(tokens[:1], (item.slug, item.name))

    buckets: dict[str, list[tuple[tuple[str, ...], str, str]]] = {}
    for tokens, (slug, name) in entries.items():
        buckets.setdefault(tokens[0], []).append((tokens, slug, name))

    by_first = {
        first: tuple(sorted(rows, key=lambda row: (-len(row[0]), row[0])))
        for first, rows in buckets.items()
    }
    max_len = max((len(t) for t in entries), default=0)
    return Lexicon(
        by_first=by_first, max_len=max_len, slugs=frozenset(item.slug for item in items)
    )


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
            hit = _synthetic_at(text, tokens, i, lexicon)
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

        if _PRIME_TOKEN not in entry_tokens and _followed_by_prime(tokens, i + n):
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


def _synthetic_at(
    text: str, tokens: list[tuple[str, int]], i: int, lexicon: Lexicon
) -> tuple[int, str, str, float, int, int] | None:
    """A "<Name> Prime" the catalog does not sell yet.

    Reached only when the normal scan found nothing here, which for a name followed
    by "prime" is exactly the trailing-Prime guard firing. Runs longest-first over
    the lexicon so "Dual Keres Prime" predicts dual_keres_prime_set, and falls back
    to the single capitalised token so a name absent from the catalog entirely
    (an unreleased frame) is still predicted.

    Two guards keep this from over-firing on prose that merely puts a capitalised
    word next to "Prime": punctuation between them makes it a list ("Weapons, Prime,
    Complete and Accessories Packs"), and a cosmetic/bundle noun right after "Prime"
    means the thing named is not a tradeable set at all.
    """
    start = tokens[i][1]
    if not text[start].isupper() or tokens[i][0] in NEVER_PRIME_BASE:
        return None

    entry_tokens: tuple[str, ...] | None = None
    for candidate_tokens, _slug, _name in lexicon.by_first.get(tokens[i][0], ()):
        n = len(candidate_tokens)
        if i + n > len(tokens) or _PRIME_TOKEN in candidate_tokens:
            continue
        if tuple(t for t, _ in tokens[i : i + n]) != candidate_tokens:
            continue
        if _followed_by_prime(tokens, i + n) and _prime_is_adjacent(text, tokens, i + n):
            entry_tokens = candidate_tokens
            break

    if entry_tokens is None:
        if not (
            _followed_by_prime(tokens, i + 1) and _prime_is_adjacent(text, tokens, i + 1)
        ):
            return None
        entry_tokens = (tokens[i][0],)

    # Look two tokens past "prime", not just one: "Spinele Prime Facial Accessory"
    # only reveals itself as a cosmetic at "Accessory", the word after "Facial". But
    # stop at a sentence terminator, the same discipline _followed_by_prime already
    # applies at one token: "Citrine Prime. Bundle deals..." must not let "Bundle" in
    # the NEXT sentence suppress synthesis of citrine_prime_set.
    prime_index = i + len(entry_tokens)
    prime_token, prime_start = tokens[prime_index]
    boundary = prime_start + len(prime_token)
    for offset in (1, 2):
        after_prime = prime_index + offset
        if after_prime >= len(tokens):
            break
        next_token, next_start = tokens[after_prime]
        if any(ch in _SENTENCE_END for ch in text[boundary:next_start]):
            break
        if next_token in NEVER_PRIME_FOLLOWED_BY:
            return None
        boundary = next_start + len(next_token)

    slug = "_".join(entry_tokens) + "_prime_set"
    if slug in lexicon.slugs:
        # Released between announcement and ingest: the normal path owns it.
        return None

    span = len(entry_tokens) + 1  # the name plus the "prime" token it is followed by
    last_token, last_start = tokens[i + span - 1]
    end = last_start + len(last_token)
    return span, slug, text[start:end], SYNTHETIC_SCORE, start, end


def _prime_is_adjacent(text: str, tokens: list[tuple[str, int]], prime_index: int) -> bool:
    """True when only whitespace separates the previous token from the "prime" token
    at `prime_index`. Call only where that token is known to be "prime".

    Punctuation in the gap means a list, not a name: "Weapons, Prime, Complete and
    Accessories Packs" is bundle-pack prose, not the item "Weapons Prime".
    """
    prev_token, prev_start = tokens[prime_index - 1]
    prev_end = prev_start + len(prev_token)
    return text[prev_end : tokens[prime_index][1]].strip() == ""


def _followed_by_prime(tokens: list[tuple[str, int]], index: int) -> bool:
    """True when the very next token is "prime".

    Only the next token: "the Steflos is cheaper than any Prime shotgun" is still about
    the Steflos, and scanning further would throw away real matches.
    """
    return index < len(tokens) and tokens[index][0] == _PRIME_TOKEN


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
