"""The gate's output fed straight into the linker.

`Candidate.name` crosses from match.py to link.py, where `classify_method` reads it as
the wording the ARTICLE used. Testing the two halves separately hid a disagreement
about what that string means for a whole review round: link.py's tests passed
`subject_raw="Mesa"` by hand, a value the gate could not then produce, so BASE_ALIAS
was unreachable in the assembled system while both halves looked correct.

Nothing here hand-constructs the value in between.
"""

from __future__ import annotations

import pytest

from wfm.models import Item
from wfm.news.link import BASE_ALIAS_WEIGHT, build_links
from wfm.news.match import build_lexicon, find_candidates
from wfm.news.types import EventType, LinkMethod, NewsDirection

CATALOG = {
    i.slug: i
    for i in [
        Item(
            slug="banshee_prime_set",
            name="Banshee Prime Set",
            url_name="banshee_prime_set",
            tags=("set", "prime", "warframe"),
            is_set=True,
        ),
        Item(
            slug="banshee_prime_blueprint",
            name="Banshee Prime Blueprint",
            url_name="banshee_prime_blueprint",
            tags=("blueprint", "prime"),
        ),
    ]
}


def _link(text: str, event_type=EventType.REWORK, direction=NewsDirection.UNCLEAR):
    """gate -> linker, with no hand-written value between them."""
    lexicon = build_lexicon(CATALOG.values())
    candidates = find_candidates(text, lexicon)
    assert len(candidates) == 1, [c.slug for c in candidates]
    candidate = candidates[0]
    links = build_links(
        event_type, direction, candidate.slug, candidate.name, candidate.score, CATALOG
    )
    return candidate, {link.slug: link for link in links}


def test_an_article_naming_only_the_base_frame_links_as_a_base_alias():
    candidate, links = _link("We reworked Banshee this update.")

    assert candidate.slug == "banshee_prime_set"
    # The article's wording, not the catalog row's: "Banshee Prime Set" would contain
    # "prime" and classify_method could never see a base alias.
    assert candidate.name == "Banshee"

    set_link = links["banshee_prime_set"]
    assert set_link.link_method is LinkMethod.BASE_ALIAS
    assert set_link.weight == pytest.approx(BASE_ALIAS_WEIGHT)


def test_an_article_writing_prime_links_exactly_through_the_same_path():
    candidate, links = _link("Banshee Prime enters the vault.", EventType.VAULT_IN,
                             NewsDirection.UP)

    # Still the catalog row here: the SET-alias pass ("banshee prime" -> the set) was
    # left carrying item.name, which is harmless because that string contains "prime"
    # and so classifies EXACT either way. Only the BASE-alias pass had to change.
    assert candidate.name == "Banshee Prime Set"
    set_link = links["banshee_prime_set"]
    assert set_link.link_method is LinkMethod.EXACT
    assert set_link.weight == pytest.approx(1.0)


def test_the_two_wordings_are_actually_distinguishable():
    # The bug was not that either branch was wrong on its own, but that both wordings
    # produced the same subject string. Pin the difference itself.
    alias, _ = _link("We reworked Banshee this update.")
    explicit, _ = _link("Banshee Prime enters the vault.")
    assert alias.name != explicit.name


def test_base_alias_weight_reaches_the_expanded_parts_too():
    _, links = _link("We reworked Banshee this update.")
    part = links["banshee_prime_blueprint"]
    assert part.link_method is LinkMethod.SET_EXPANSION
    assert part.weight < 1.0
