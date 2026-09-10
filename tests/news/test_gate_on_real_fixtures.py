"""What the gate actually finds in real captured articles.

The lexicon here is small and hand-built, but every name in it was checked against the
live catalog on 2026-09-06. The point is not coverage of the catalog, it is that the
expected sets below are an answer key someone read the fixture text to produce, so a
change in gate behaviour has to be argued with rather than absorbed.
"""

from pathlib import Path

from wfm.models import Item
from wfm.news.html import strip_tags
from wfm.news.match import build_lexicon, find_candidates
from wfm.news.sources.feed import parse_atom, parse_rss

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "news"

# Verified present in the live catalog. is_set matters: it is what registers the base
# alias, so "Vectis Prime" reaches "Vectis Prime Set".
CATALOG = [
    Item(slug="archon_continuity", name="Archon Continuity", url_name="a"),
    Item(slug="merulina_guardian", name="Merulina Guardian", url_name="b"),
    Item(slug="savage_silence", name="Savage Silence", url_name="c"),
    Item(slug="rage", name="Rage", url_name="d"),
    Item(slug="adaptation", name="Adaptation", url_name="e"),
    # Vectis and Athodai are weapons, not frames, so they carry no "warframe" tag and
    # register no base alias.
    Item(slug="vectis_prime_set", name="Vectis Prime Set", url_name="f", is_set=True),
    Item(slug="athodai_prime_set", name="Athodai Prime Set", url_name="g", is_set=True),
    Item(slug="athodai_set", name="Athodai Set", url_name="h", is_set=True),
    Item(
        slug="banshee_prime_set",
        name="Banshee Prime Set",
        url_name="i",
        tags=("set", "prime", "warframe"),
        is_set=True,
    ),
    Item(
        slug="baruuk_prime_set",
        name="Baruuk Prime Set",
        url_name="j",
        tags=("set", "prime", "warframe"),
        is_set=True,
    ),
    Item(
        slug="yareli_prime_set",
        name="Yareli Prime Set",
        url_name="k",
        tags=("set", "prime", "warframe"),
        is_set=True,
    ),
    Item(slug="steflos_set", name="Steflos Set", url_name="l", is_set=True),
    Item(slug="corufell_set", name="Corufell Set", url_name="m", is_set=True),
]

LEXICON = build_lexicon(CATALOG)


def forum_items():
    xml = (FIXTURES / "forums_updates.xml").read_text(encoding="utf-8")
    return [(e.title, strip_tags(e.body_html)) for e in parse_rss(xml)]


def slugs(title: str, body: str) -> set[str]:
    return {c.slug for c in find_candidates(f"{title}\n\n{body}", LEXICON)}


def test_hotfix_4353_now_reaches_the_base_frame_names_it_mentions():
    title, body = forum_items()[0]
    found = slugs(title, body)
    # "Athodai Prime's unique trait" and "the Vectis (Prime) not having a fully
    # reloaded magazine". The parentheses are not word characters, so the tokens
    # really are "vectis prime".
    assert "athodai_prime_set" in found
    assert "vectis_prime_set" in found
    # This used to assert `"banshee_prime_set" not in found`, and that miss was the
    # honest part of plan 2's measurement: the note says "Banshee's Silence" while
    # the catalog sells "Banshee Prime Set". The base-name alias closes it.
    assert "banshee_prime_set" in found


def test_the_base_athodai_is_not_matched_when_the_text_says_athodai_prime():
    title, body = forum_items()[0]
    assert "athodai_set" not in slugs(title, body)


def test_hotfix_4354_now_reaches_the_frames_it_names():
    title, body = forum_items()[1]
    found = slugs(title, body)
    # Was `found == set()`. Yareli and Baruuk now resolve through their Prime sets;
    # Merulina Guardian already did; Daiku is still not in the catalog at all, but the
    # note does say "Daiku Prime" ("Fixed Daiku and Daiku Prime's animations..."),
    # which is exactly the case synthesis exists for: an unreleased frame's Prime,
    # predicted rather than silently dropped.
    assert {"yareli_prime_set", "baruuk_prime_set"} <= found
    assert "daiku_prime_set" in found


def test_the_big_update_note_finds_archon_continuity():
    title, body = forum_items()[2]
    assert "archon_continuity" in slugs(title, body)


def test_a_reddit_workshop_finds_the_augment_mods_it_names():
    xml = (FIXTURES / "reddit_hot.xml").read_text(encoding="utf-8")
    entries = parse_atom(xml)
    banshee = next(e for e in entries if "Banshee" in e.title)
    assert "savage_silence" in slugs(banshee.title, strip_tags(banshee.body_html))


def test_a_prime_access_announcement_predicts_the_slugs_it_announces():
    from wfm.news.html import extract_element_text

    html = (FIXTURES / "warframe_article.html").read_text(encoding="utf-8")
    body = extract_element_text(html, "post-body")
    found = slugs("Citrine Prime Access", body)

    # This assertion used to be `found == set()`, and that emptiness was the single
    # most important measured result of plan 2: none of the three announced items is
    # in the catalog on announcement day, and the gate's earlier answer
    # ({steflos_set, corufell_set}) attached the event to the BASE weapons, which
    # move in the opposite direction. Silence was better than that. Predicting the
    # set slug is better than silence: the event and its date get recorded, and the
    # reconciliation pass on `wfm sync` replaces the guess once the real slug ships.
    assert found == {"citrine_prime_set", "steflos_prime_set", "corufell_prime_set"}


def test_an_empty_body_matches_nothing_and_does_not_raise():
    assert slugs("", "") == set()
