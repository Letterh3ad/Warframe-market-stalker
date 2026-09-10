import pytest

from wfm.models import Item
from wfm.news.link import (
    DIRECTION_TABLE,
    ROLE_MOD,
    ROLE_OTHER,
    ROLE_PART,
    ROLE_RELIC,
    ROLE_SET,
    build_links,
    classify_method,
    role_of,
)
from wfm.news.types import EventType, LinkMethod, NewsDirection


def item(slug, name, tags=(), rank=0, is_set=False):
    return Item(
        slug=slug, name=name, url_name=slug, tags=tuple(tags),
        canonical_rank=rank, is_set=is_set,
    )


CATALOG = {
    i.slug: i
    for i in [
        item("mesa_prime_set", "Mesa Prime Set", ("set", "prime", "warframe"), is_set=True),
        item("mesa_prime_blueprint", "Mesa Prime Blueprint", ("blueprint", "prime")),
        item("mesa_prime_systems_blueprint", "Mesa Prime Systems", ("component", "prime")),
        item("rage", "Rage", ("mod",), rank=3),
        item("lith_a1_relic", "Lith A1 Relic", ("relic",)),
    ]
}


def test_the_table_covers_every_event_type():
    assert set(DIRECTION_TABLE) == set(EventType)


def test_role_comes_from_tags():
    assert role_of(CATALOG["mesa_prime_set"]) == ROLE_SET
    assert role_of(CATALOG["mesa_prime_blueprint"]) == ROLE_PART
    assert role_of(CATALOG["rage"]) == ROLE_MOD
    assert role_of(None) == ROLE_SET  # a synthetic slug is a set by construction


def test_an_exact_catalog_hit_is_exact():
    assert classify_method("rage", "Rage", 1.0, CATALOG) is LinkMethod.EXACT


def test_a_scored_down_hit_is_fuzzy():
    assert classify_method("rage", "Raage", 0.91, CATALOG) is LinkMethod.FUZZY


def test_a_slug_the_catalog_does_not_sell_is_synthetic():
    assert classify_method(
        "steflos_prime_set", "Steflos Prime", 1.0, CATALOG
    ) is LinkMethod.SYNTHETIC


# "Mesa" is a subject the gate really emits: the base-alias lexicon entry carries the
# base name. tests/news/test_gate_link_seam.py pins that end to end.
def test_a_prime_set_reached_without_the_word_prime_is_a_base_alias():
    assert classify_method(
        "mesa_prime_set", "Mesa", 1.0, CATALOG
    ) is LinkMethod.BASE_ALIAS


def test_a_prime_set_named_with_prime_is_exact():
    assert classify_method(
        "mesa_prime_set", "Mesa Prime Set", 1.0, CATALOG
    ) is LinkMethod.EXACT


def test_a_vaulting_pushes_the_set_and_its_parts_up():
    links = build_links(
        EventType.VAULT_IN, NewsDirection.UP, "mesa_prime_set", "Mesa Prime", 1.0, CATALOG
    )
    by_slug = {l.slug: l for l in links}
    assert by_slug["mesa_prime_set"].direction is NewsDirection.UP
    assert by_slug["mesa_prime_blueprint"].direction is NewsDirection.UP


def test_a_prime_access_release_pushes_the_same_slugs_down():
    # The reason per-item direction exists at all: two event types, same slugs,
    # opposite signs.
    links = build_links(
        EventType.PRIME_ACCESS, NewsDirection.DOWN, "mesa_prime_set", "Mesa Prime", 1.0,
        CATALOG,
    )
    assert all(l.direction is NewsDirection.DOWN for l in links)


def test_set_expansion_reaches_the_parts_at_a_lower_weight():
    links = build_links(
        EventType.VAULT_IN, NewsDirection.UP, "mesa_prime_set", "Mesa Prime", 1.0, CATALOG
    )
    by_slug = {l.slug: l for l in links}
    assert by_slug["mesa_prime_set"].weight == 1.0
    assert by_slug["mesa_prime_blueprint"].weight == 0.8
    assert by_slug["mesa_prime_blueprint"].link_method is LinkMethod.SET_EXPANSION
    assert {"mesa_prime_blueprint", "mesa_prime_systems_blueprint"} <= set(by_slug)


def test_a_mod_gets_no_link_from_a_vaulting():
    # The table's "none": a vaulting says nothing about an unrelated mod.
    assert build_links(
        EventType.VAULT_IN, NewsDirection.UP, "rage", "Rage", 1.0, CATALOG
    ) == []


def test_a_nerf_reaches_a_mod():
    links = build_links(
        EventType.NERF, NewsDirection.DOWN, "rage", "Rage", 1.0, CATALOG
    )
    assert [l.slug for l in links] == ["rage"]
    assert links[0].direction is NewsDirection.DOWN
    assert links[0].rank == 3  # items.canonical_rank, not 0


def test_a_rework_links_but_contributes_nothing():
    links = build_links(
        EventType.REWORK, NewsDirection.UNCLEAR, "rage", "Rage", 1.0, CATALOG
    )
    assert links[0].direction is NewsDirection.UNCLEAR
    assert links[0].direction.sign == 0


def test_a_drop_rate_change_on_a_mod_emits_no_link():
    # ROLE_MOD is None for drop_rate_change: the event says nothing about mods.
    assert build_links(
        EventType.DROP_RATE_CHANGE, NewsDirection.UP, "rage", "Rage", 1.0, CATALOG
    ) == []


def test_a_drop_rate_change_on_a_part_takes_its_sign_from_the_event():
    # Only the model knows which way the rate moved, so the table defers to it.
    up = build_links(
        EventType.DROP_RATE_CHANGE, NewsDirection.UP, "mesa_prime_blueprint",
        "Mesa Prime Blueprint", 1.0, CATALOG,
    )
    down = build_links(
        EventType.DROP_RATE_CHANGE, NewsDirection.DOWN, "mesa_prime_blueprint",
        "Mesa Prime Blueprint", 1.0, CATALOG,
    )
    assert up[0].direction is NewsDirection.UP
    assert down[0].direction is NewsDirection.DOWN


def test_a_buff_pushes_a_mod_up_regardless_of_the_events_own_direction():
    # BUFF's cell is a fixed UP, not USE_EVENT_DIRECTION -- passing DOWN here proves
    # the table cell drives it, not the caller-supplied event_direction.
    links = build_links(
        EventType.BUFF, NewsDirection.DOWN, "rage", "Rage", 1.0, CATALOG
    )
    assert [l.slug for l in links] == ["rage"]
    assert links[0].direction is NewsDirection.UP


def test_a_vault_out_pushes_the_set_and_its_parts_down():
    links = build_links(
        EventType.VAULT_OUT, NewsDirection.UP, "mesa_prime_set", "Mesa Prime", 1.0,
        CATALOG,
    )
    by_slug = {l.slug: l for l in links}
    assert by_slug["mesa_prime_set"].direction is NewsDirection.DOWN
    assert by_slug["mesa_prime_blueprint"].direction is NewsDirection.DOWN


def test_a_vault_out_gives_a_mod_no_link():
    assert build_links(
        EventType.VAULT_OUT, NewsDirection.DOWN, "rage", "Rage", 1.0, CATALOG
    ) == []


def test_new_content_pushes_a_set_and_a_mod_up_but_not_a_relic():
    set_links = build_links(
        EventType.NEW_CONTENT, NewsDirection.DOWN, "mesa_prime_set", "Mesa Prime", 1.0,
        CATALOG,
    )
    assert {l.slug: l.direction for l in set_links}["mesa_prime_set"] is NewsDirection.UP

    mod_links = build_links(
        EventType.NEW_CONTENT, NewsDirection.DOWN, "rage", "Rage", 1.0, CATALOG
    )
    assert mod_links[0].direction is NewsDirection.UP

    # NEW_CONTENT's relic cell is None -- moot in this plan since relics never reach
    # build_links as a subject slug, but the row itself must say so explicitly.
    assert DIRECTION_TABLE[EventType.NEW_CONTENT][ROLE_RELIC] is None


def test_other_event_type_is_unclear_and_contributes_nothing():
    links = build_links(
        EventType.OTHER, NewsDirection.UP, "rage", "Rage", 1.0, CATALOG
    )
    assert links[0].direction is NewsDirection.UNCLEAR
    assert links[0].direction.sign == 0


def test_role_other_is_an_explicit_no_link_cell_on_every_row():
    for event_type, row in DIRECTION_TABLE.items():
        assert row[ROLE_OTHER] is None, event_type


def test_a_synthetic_slug_links_at_full_weight_with_no_expansion():
    links = build_links(
        EventType.PRIME_ACCESS, NewsDirection.DOWN, "steflos_prime_set",
        "Steflos Prime", 1.0, CATALOG,
    )
    assert [l.slug for l in links] == ["steflos_prime_set"]
    assert links[0].link_method is LinkMethod.SYNTHETIC
    assert links[0].weight == 1.0
    assert links[0].rank == 0  # no catalog row to take a canonical_rank from


def test_a_base_alias_link_is_weaker_than_an_explicit_prime():
    alias = build_links(
        EventType.REWORK, NewsDirection.UNCLEAR, "mesa_prime_set", "Mesa", 1.0, CATALOG
    )
    explicit = build_links(
        EventType.REWORK, NewsDirection.UNCLEAR, "mesa_prime_set", "Mesa Prime Set",
        1.0, CATALOG,
    )
    assert alias[0].weight == 0.9
    assert explicit[0].weight == 1.0


def test_a_fuzzy_hit_is_weighted_by_its_score():
    links = build_links(
        EventType.NERF, NewsDirection.DOWN, "rage", "Raage", 0.9, CATALOG
    )
    assert links[0].weight == pytest.approx(0.9)
    assert links[0].link_score == pytest.approx(0.9)


def test_a_relic_is_never_linked_in_this_plan():
    # Nothing in the catalog says which relic drops which part, so build_links
    # cannot reach one. The relic column stays in the table for 9b.
    links = build_links(
        EventType.VAULT_IN, NewsDirection.UP, "mesa_prime_set", "Mesa Prime", 1.0, CATALOG
    )
    assert not any(l.slug.endswith("_relic") for l in links)


# steflos_set (a real non-Prime companion) and steflos_prime_set (the Task 9
# synthetic-prediction shape, here landed as a real catalog row) share a naive
# string prefix once you strip "_set": "steflos_" is a prefix of "steflos_prime_".
# A prefix-only match would pull the Prime's parts into the companion's expansion
# and vice versa. This catalog is the minimal fixture that can catch that.
COLLIDING_CATALOG = {
    i.slug: i
    for i in [
        item("steflos_set", "Steflos Set", ("set", "weapon"), is_set=True),
        item("steflos_barrel", "Steflos Barrel", ("component",)),
        item(
            "steflos_prime_set", "Steflos Prime Set", ("set", "prime", "weapon"),
            is_set=True,
        ),
        item("steflos_prime_blueprint", "Steflos Prime Blueprint", ("blueprint", "prime")),
        item(
            "steflos_prime_lower_limb", "Steflos Prime Lower Limb",
            ("component", "prime"),
        ),
    ]
}


def test_set_expansion_does_not_cross_a_prefix_collision_into_the_prime_set():
    links = build_links(
        EventType.VAULT_IN, NewsDirection.UP, "steflos_prime_set", "Steflos Prime",
        1.0, COLLIDING_CATALOG,
    )
    slugs = {l.slug for l in links}
    assert slugs == {
        "steflos_prime_set", "steflos_prime_blueprint", "steflos_prime_lower_limb"
    }
    assert "steflos_barrel" not in slugs


def test_set_expansion_does_not_cross_a_prefix_collision_into_the_base_set():
    links = build_links(
        EventType.VAULT_IN, NewsDirection.UP, "steflos_set", "Steflos", 1.0,
        COLLIDING_CATALOG,
    )
    slugs = {l.slug for l in links}
    assert slugs == {"steflos_set", "steflos_barrel"}
    assert "steflos_prime_blueprint" not in slugs
    assert "steflos_prime_lower_limb" not in slugs
