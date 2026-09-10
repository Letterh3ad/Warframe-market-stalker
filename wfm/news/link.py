"""Event subject -> the catalog slugs it moves, and which way each one moves.

The model produces events (a name, as text); this produces links (resolved slugs).
Keeping them apart is what lets linkage re-run when the catalog grows or a threshold
changes without re-invoking a model, which is exactly what the reconciliation pass
for synthetic Prime slugs needs.

Pure: the catalog arrives as a mapping, never from wfm.store.
"""

from __future__ import annotations

from collections.abc import Mapping

from wfm.models import Item
from wfm.news.types import EventType, ItemLink, LinkMethod, NewsDirection

ROLE_SET = "set"
ROLE_PART = "part"
ROLE_RELIC = "relic"
ROLE_MOD = "mod"
ROLE_OTHER = "other"

# Only the model knows which way a drop rate moved, so the table defers to the
# event's own direction for that one row instead of inventing a sign.
USE_EVENT_DIRECTION = "event"

UP, DOWN, UNCLEAR = NewsDirection.UP, NewsDirection.DOWN, NewsDirection.UNCLEAR

# event_type x role -> per-item direction. None means "emit no link": the event says
# nothing about items in that role, and a link with sign 0 is not the same claim as
# no link at all (one shows up in the audit payload, the other does not).
#
# Data, not branching logic, so correcting a row after the backtest is one line.
#
# vault_in is a WORKING HYPOTHESIS, flagged in the design doc for review after ~6
# months of parallel news and price data. Do not "fix" it from first principles.
DIRECTION_TABLE: dict[EventType, dict[str, NewsDirection | str | None]] = {
    EventType.VAULT_IN: {
        ROLE_SET: UP, ROLE_PART: UP, ROLE_RELIC: UP, ROLE_MOD: None, ROLE_OTHER: None
    },
    EventType.VAULT_OUT: {
        ROLE_SET: DOWN, ROLE_PART: DOWN, ROLE_RELIC: DOWN, ROLE_MOD: None, ROLE_OTHER: None
    },
    EventType.PRIME_ACCESS: {
        ROLE_SET: DOWN, ROLE_PART: DOWN, ROLE_RELIC: DOWN, ROLE_MOD: None, ROLE_OTHER: None
    },
    EventType.BUFF: {
        ROLE_SET: UP, ROLE_PART: UP, ROLE_RELIC: UP, ROLE_MOD: UP, ROLE_OTHER: None
    },
    EventType.NERF: {
        ROLE_SET: DOWN, ROLE_PART: DOWN, ROLE_RELIC: DOWN, ROLE_MOD: DOWN, ROLE_OTHER: None
    },
    EventType.REWORK: {
        ROLE_SET: UNCLEAR,
        ROLE_PART: UNCLEAR,
        ROLE_RELIC: UNCLEAR,
        ROLE_MOD: UNCLEAR,
        ROLE_OTHER: None,
    },
    EventType.DROP_RATE_CHANGE: {
        ROLE_SET: USE_EVENT_DIRECTION,
        ROLE_PART: USE_EVENT_DIRECTION,
        ROLE_RELIC: USE_EVENT_DIRECTION,
        ROLE_MOD: None,
        ROLE_OTHER: None,
    },
    EventType.NEW_CONTENT: {
        ROLE_SET: UP, ROLE_PART: UP, ROLE_RELIC: None, ROLE_MOD: UP, ROLE_OTHER: None
    },
    EventType.OTHER: {
        ROLE_SET: UNCLEAR,
        ROLE_PART: UNCLEAR,
        ROLE_RELIC: UNCLEAR,
        ROLE_MOD: UNCLEAR,
        ROLE_OTHER: None,
    },
}

# A part moves with its set but is a weaker claim than the set itself.
SET_SIBLING_WEIGHT = 0.8
# A bare frame name is weaker evidence than the article writing "Banshee Prime".
BASE_ALIAS_WEIGHT = 0.9


def role_of(item: Item | None) -> str:
    """What kind of thing this is, for the direction table.

    None is a synthetic slug, which is a set by construction: nothing else is ever
    predicted.
    """
    if item is None:
        return ROLE_SET
    tags = set(item.tags)
    if "relic" in tags or item.slug.endswith("_relic"):
        return ROLE_RELIC
    if "set" in tags or item.is_set:
        return ROLE_SET
    if "component" in tags or "blueprint" in tags:
        return ROLE_PART
    if "mod" in tags:
        return ROLE_MOD
    return ROLE_OTHER


def classify_method(
    slug: str, subject_raw: str, score: float, catalog: Mapping[str, Item]
) -> LinkMethod:
    if slug not in catalog:
        return LinkMethod.SYNTHETIC
    if slug.endswith("_prime_set") and "prime" not in subject_raw.lower():
        return LinkMethod.BASE_ALIAS
    return LinkMethod.EXACT if score >= 1.0 else LinkMethod.FUZZY


def build_links(
    event_type: EventType,
    event_direction: NewsDirection,
    slug: str,
    subject_raw: str,
    score: float,
    catalog: Mapping[str, Item],
) -> list[ItemLink]:
    """Every item this event touches, with a per-item direction and weight."""
    row = DIRECTION_TABLE[event_type]
    method = classify_method(slug, subject_raw, score, catalog)
    base_weight = _base_weight(method, score)

    item = catalog.get(slug)
    links: list[ItemLink] = []

    direction = _direction(row, role_of(item), event_direction)
    if direction is None:
        return []
    links.append(
        ItemLink(
            slug=slug,
            rank=item.canonical_rank if item is not None else 0,
            link_method=method,
            link_score=score,
            direction=direction,
            weight=base_weight,
        )
    )

    # Set expansion. A synthetic slug has no catalog row, so it expands to nothing
    # until reconciliation replaces it with a real one -- which is the whole point of
    # keeping linkage re-runnable.
    if item is None or not slug.endswith("_set"):
        return links

    part_direction = _direction(row, ROLE_PART, event_direction)
    if part_direction is None:
        return links

    prefix = slug[: -len("set")]
    set_prefixes = {
        s: s[: -len("set")] for s in catalog if s.endswith("_set")
    }
    for sibling_slug, sibling in catalog.items():
        if sibling_slug == slug or sibling_slug.endswith("_set"):
            continue
        if not sibling_slug.startswith(prefix):
            continue
        # Longest-prefix ownership: "steflos_prime_blueprint" starts with both
        # "steflos_" (steflos_set) and "steflos_prime_" (steflos_prime_set); the
        # longer, more specific prefix wins, so it belongs to the Prime set only.
        owner = max(
            (s for s, p in set_prefixes.items() if sibling_slug.startswith(p)),
            key=lambda s: len(set_prefixes[s]),
        )
        if owner != slug:
            continue
        links.append(
            ItemLink(
                slug=sibling_slug,
                rank=sibling.canonical_rank,
                link_method=LinkMethod.SET_EXPANSION,
                link_score=score,
                direction=part_direction,
                weight=base_weight * SET_SIBLING_WEIGHT,
            )
        )
    return links


def _direction(
    row: Mapping[str, NewsDirection | str | None],
    role: str,
    event_direction: NewsDirection,
) -> NewsDirection | None:
    value = row.get(role)
    if value == USE_EVENT_DIRECTION:
        return event_direction
    return value


def _base_weight(method: LinkMethod, score: float) -> float:
    if method is LinkMethod.BASE_ALIAS:
        return BASE_ALIAS_WEIGHT
    if method is LinkMethod.FUZZY:
        return score
    return 1.0
