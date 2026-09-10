from wfm.models import Item
from wfm.news.match import SYNTHETIC_SCORE, build_lexicon, normalize

CATALOG = [
    Item(slug="mesa_prime_set", name="Mesa Prime Set", url_name="mesa_prime_set", is_set=True),
    Item(
        slug="mesa_prime_neuroptics",
        name="Mesa Prime Neuroptics Blueprint",
        url_name="mesa_prime_neuroptics",
    ),
    Item(slug="condition_overload", name="Condition Overload", url_name="condition_overload"),
    Item(slug="nekros_prime_set", name="Nekros Prime Set", url_name="nekros_prime_set", is_set=True),
]


def test_normalize_lowercases_and_keeps_offsets():
    assert normalize("Mesa Prime!") == [("mesa", 0), ("prime", 5)]


def test_normalize_offsets_index_the_original_text():
    text = "The MESA Prime set"
    tokens = normalize(text)
    assert [t for t, _ in tokens] == ["the", "mesa", "prime", "set"]
    # Every offset must still address the original casing, which is what the
    # proper-noun check in find_candidates depends on.
    assert all(text[start].lower() == token[0] for token, start in tokens)


def test_normalize_splits_on_punctuation_and_keeps_digits():
    assert [t for t, _ in normalize("Update 38.5: Mesa-Prime")] == [
        "update", "38", "5", "mesa", "prime",
    ]


def test_lexicon_registers_full_names():
    lex = build_lexicon(CATALOG)
    entries = {tokens: slug for tokens, slug, _ in lex.by_first["condition"]}
    assert entries[("condition", "overload")] == "condition_overload"


def test_set_items_also_register_their_base_name():
    lex = build_lexicon(CATALOG)
    entries = {tokens: slug for tokens, slug, _ in lex.by_first["mesa"]}
    assert entries[("mesa", "prime")] == "mesa_prime_set"
    assert entries[("mesa", "prime", "set")] == "mesa_prime_set"


def test_non_set_items_get_no_base_alias():
    lex = build_lexicon(CATALOG)
    entries = {tokens for tokens, _, _ in lex.by_first["mesa"]}
    assert ("mesa", "prime", "neuroptics") not in entries


def test_entries_are_sorted_longest_first():
    lex = build_lexicon(CATALOG)
    lengths = [len(tokens) for tokens, _, _ in lex.by_first["mesa"]]
    assert lengths == sorted(lengths, reverse=True)


def test_max_len_reflects_the_longest_entry():
    lex = build_lexicon(CATALOG)
    assert lex.max_len == 4  # "mesa prime neuroptics blueprint"


from wfm.news.match import find_candidates

SINGLES = [
    Item(slug="nekros", name="Nekros", url_name="nekros"),
    Item(slug="fury", name="Fury", url_name="fury"),
    Item(slug="arca_plasmor", name="Arca Plasmor", url_name="arca_plasmor"),
]


def _slugs(candidates):
    return {c.slug for c in candidates}


def test_finds_an_exact_multi_token_name():
    lex = build_lexicon(CATALOG)
    found = find_candidates("Condition Overload damage has been reduced.", lex)
    assert _slugs(found) == {"condition_overload"}
    assert found[0].score == 1.0


def test_a_bare_frame_name_reaches_the_set_via_the_alias():
    lex = build_lexicon(CATALOG)
    found = find_candidates("Mesa Prime enters the Prime Vault soon.", lex)
    assert _slugs(found) == {"mesa_prime_set"}


def test_longest_match_wins_and_does_not_double_report():
    lex = build_lexicon(CATALOG)
    found = find_candidates("The Mesa Prime Set is returning.", lex)
    assert len(found) == 1
    assert found[0].slug == "mesa_prime_set"


def test_text_with_no_catalog_item_yields_nothing():
    lex = build_lexicon(CATALOG)
    assert find_candidates("The server maintenance is now complete.", lex) == []


def test_single_token_name_requires_capitalisation():
    lex = build_lexicon(SINGLES)
    assert _slugs(find_candidates("Nekros received a rework.", lex)) == {"nekros"}
    assert find_candidates("the enemy attacks with nekros energy", lex) == []


def test_short_distinctive_names_match_at_any_length():
    # Requiem mods are 4 characters and perfectly distinctive. A length rule would have
    # silently dropped nine of them.
    lex = build_lexicon([Item(slug="xata", name="Xata", url_name="xata")])
    assert _slugs(find_candidates("Drop rates for Xata have changed.", lex)) == {"xata"}


def test_ambiguous_single_token_matches_mid_sentence_only():
    lex = build_lexicon([Item(slug="rage", name="Rage", url_name="rage")])
    assert _slugs(find_candidates("The Rage mod was buffed.", lex)) == {"rage"}
    assert find_candidates("Rage was the theme of this update.", lex) == []


def test_ambiguous_single_token_is_also_rejected_after_a_full_stop():
    lex = build_lexicon([Item(slug="flow", name="Flow", url_name="flow")])
    assert find_candidates("Energy is changing. Flow through the level.", lex) == []


def test_gate_recall_over_a_known_mention_set():
    # Recall is the failure mode nobody sees: a false positive shows up in the output,
    # a missed mention is invisible. This fixture is the guard.
    lex = build_lexicon(CATALOG + SINGLES)
    text = (
        "Hotfix 38.5: Condition Overload damage reduced. "
        "Mesa Prime enters the Prime Vault. "
        "Nekros received a rework, and Arca Plasmor status chance is up."
    )
    assert _slugs(find_candidates(text, lex)) == {
        "condition_overload",
        "mesa_prime_set",
        "nekros",
        "arca_plasmor",
    }


def test_near_miss_matches_fuzzily_and_scores_below_one():
    lex = build_lexicon(CATALOG)
    found = find_candidates("Condition Overlod damage has been reduced.", lex)
    assert _slugs(found) == {"condition_overload"}
    assert 0.88 <= found[0].score < 1.0


def test_typo_beyond_the_threshold_is_rejected():
    lex = build_lexicon(CATALOG)
    assert find_candidates("Compression Overflow damage reduced.", lex) == []


def test_repeated_mentions_collapse_to_the_best_score():
    lex = build_lexicon(CATALOG)
    found = find_candidates(
        "Condition Overlod is changing. Condition Overload is changing.", lex
    )
    assert len(found) == 1
    assert found[0].score == 1.0


def test_context_surrounds_the_match_and_is_bounded():
    lex = build_lexicon(CATALOG)
    text = "x " * 400 + "Condition Overload is changing. " + "y " * 400
    (found,) = find_candidates(text, lex, context_chars=50)
    assert "Condition Overload" in found.context
    assert len(found.context) <= 50 * 2 + len("Condition Overload") + 2


def test_results_are_ordered_by_descending_score():
    lex = build_lexicon(CATALOG)
    found = find_candidates("Condition Overlod and Nekros Prime Set both change.", lex)
    assert [c.score for c in found] == sorted([c.score for c in found], reverse=True)


def test_a_base_item_does_not_match_when_the_text_says_prime():
    # Verified against the live catalog: on announcement day "Steflos Prime" is not in
    # the catalog and "Steflos Set" is, so without this guard a Prime Access article
    # links to the base weapon, which moves differently from the Prime.
    #
    # The guard's intent is unchanged: the base slug must never appear. What changed is
    # that the answer is no longer empty — synthesis (added after this guard) now
    # predicts steflos_prime_set instead of reporting nothing, so the assertion checks
    # the base slug's absence rather than an empty result.
    lexicon = build_lexicon(
        [Item(slug="steflos_set", name="Steflos Set", url_name="steflos_set", is_set=True)]
    )
    text = "Steflos Prime enters Prime Access on September 23."
    found = {c.slug for c in find_candidates(text, lexicon)}
    assert "steflos_set" not in found


def test_a_prime_entry_still_matches_text_that_says_prime():
    lexicon = build_lexicon(
        [
            Item(
                slug="vectis_prime_set",
                name="Vectis Prime Set",
                url_name="vectis_prime_set",
                is_set=True,
            )
        ]
    )
    text = "Fixed the Vectis (Prime) not having a fully reloaded magazine."
    (found,) = find_candidates(text, lexicon)
    assert found.slug == "vectis_prime_set"


def test_a_base_item_still_matches_when_prime_does_not_follow_it():
    lexicon = build_lexicon(
        [Item(slug="steflos_set", name="Steflos Set", url_name="steflos_set", is_set=True)]
    )
    (found,) = find_candidates("The Steflos is a shotgun.", lexicon)
    assert found.slug == "steflos_set"


def test_the_guard_looks_at_the_next_token_not_the_rest_of_the_sentence():
    lexicon = build_lexicon(
        [Item(slug="steflos_set", name="Steflos Set", url_name="steflos_set", is_set=True)]
    )
    (found,) = find_candidates("The Steflos is cheaper than any Prime shotgun.", lexicon)
    assert found.slug == "steflos_set"


def test_an_announced_prime_weapon_gets_a_predicted_set_slug():
    lex = build_lexicon([Item(slug="steflos_set", name="Steflos Set", url_name="a", is_set=True)])
    found = {c.slug: c for c in find_candidates("The Steflos Prime arrives soon.", lex)}
    assert "steflos_prime_set" in found
    assert "steflos_set" not in found
    assert found["steflos_prime_set"].name == "Steflos Prime"
    assert found["steflos_prime_set"].score == SYNTHETIC_SCORE


def test_a_released_prime_is_matched_normally_not_synthesised():
    lex = build_lexicon(
        [
            Item(slug="mesa_prime_set", name="Mesa Prime Set", url_name="a", is_set=True),
            Item(slug="mesa_set", name="Mesa Set", url_name="b", is_set=True),
        ]
    )
    found = {c.slug for c in find_candidates("Mesa Prime enters the vault.", lex)}
    assert found == {"mesa_prime_set"}


def test_a_multi_token_base_predicts_a_multi_token_slug():
    lex = build_lexicon(
        [Item(slug="dual_keres_set", name="Dual Keres Set", url_name="a", is_set=True)]
    )
    found = {c.slug for c in find_candidates("Dual Keres Prime is coming.", lex)}
    assert found == {"dual_keres_prime_set"}


def test_a_name_absent_from_the_catalog_still_predicts_a_slug():
    # The frame case: Citrine has no Prime, so decision 2 registers no alias for it,
    # and the base frame is not tradeable so it is not in the catalog either.
    lex = build_lexicon([Item(slug="rage", name="Rage", url_name="a")])
    found = {c.slug for c in find_candidates("Citrine Prime Access is live.", lex)}
    assert found == {"citrine_prime_set"}


def test_a_lowercase_word_before_prime_predicts_nothing():
    lex = build_lexicon([Item(slug="rage", name="Rage", url_name="a")])
    assert find_candidates("cheaper than any prime shotgun", lex) == []


def test_prime_access_as_a_brand_is_not_read_as_an_item():
    lex = build_lexicon([Item(slug="rage", name="Rage", url_name="a")])
    found = {c.slug for c in find_candidates("Warframe Prime Access returns.", lex)}
    assert found == set()


def test_the_context_of_a_synthetic_candidate_is_the_surrounding_sentence():
    lex = build_lexicon([Item(slug="rage", name="Rage", url_name="a")])
    found = find_candidates("Citrine Prime enters the vault on the 20th.", lex)
    assert "vault" in found[0].context


def test_a_base_frame_name_resolves_to_its_prime_set():
    lex = build_lexicon(
        [
            Item(
                slug="banshee_prime_set",
                name="Banshee Prime Set",
                url_name="a",
                tags=("set", "prime", "warframe"),
                is_set=True,
            )
        ]
    )
    assert {c.slug for c in find_candidates("We revisited Banshee's kit.", lex)} == {
        "banshee_prime_set"
    }


def test_a_frame_with_no_prime_does_not_link():
    # Guard 1, and it is free: Dagath has no *_prime_set row, so no alias exists.
    lex = build_lexicon(
        [
            Item(
                slug="banshee_prime_set",
                name="Banshee Prime Set",
                url_name="a",
                tags=("set", "prime", "warframe"),
                is_set=True,
            )
        ]
    )
    assert find_candidates("We revisited Dagath's kit.", lex) == []


def test_a_prime_weapon_set_does_not_register_a_base_alias():
    # Warframes only. "Braton" has far more non-Prime meanings than "Banshee".
    lex = build_lexicon(
        [
            Item(
                slug="braton_prime_set",
                name="Braton Prime Set",
                url_name="a",
                tags=("set", "prime", "weapon", "primary"),
                is_set=True,
            )
        ]
    )
    assert find_candidates("Braton damage was adjusted.", lex) == []


def test_an_english_collision_frame_needs_mid_sentence_position():
    lex = build_lexicon(
        [
            Item(
                slug="ember_prime_set",
                name="Ember Prime Set",
                url_name="a",
                tags=("set", "prime", "warframe"),
                is_set=True,
            )
        ]
    )
    assert find_candidates("Ember damage now scales.", lex) == []
    assert {c.slug for c in find_candidates("We reworked Ember this update.", lex)} == {
        "ember_prime_set"
    }


def test_an_explicit_prime_still_beats_the_base_alias():
    lex = build_lexicon(
        [
            Item(
                slug="banshee_prime_set",
                name="Banshee Prime Set",
                url_name="a",
                tags=("set", "prime", "warframe"),
                is_set=True,
            )
        ]
    )
    found = find_candidates("Banshee Prime enters the vault.", lex)
    assert [c.slug for c in found] == ["banshee_prime_set"]
    assert found[0].name == "Banshee Prime Set"  # the real entry, not a synthetic one


def test_a_cosmetic_phrase_does_not_synthesize():
    lex = build_lexicon([Item(slug="rage", name="Rage", url_name="a")])
    found = find_candidates("Sphatika Prime Syandana looks great.", lex)
    assert found == []


def test_suppression_is_per_occurrence_not_per_name():
    # Load-bearing: the two-token cosmetic-noun lookahead only stays safe if it
    # suppresses the one occurrence next to a cosmetic noun, not the name everywhere.
    # A clean mention of the same name elsewhere must still synthesize.
    lex = build_lexicon([Item(slug="rage", name="Rage", url_name="a")])
    found = {
        c.slug
        for c in find_candidates(
            "Sphatika Prime Syandana looks great. Sphatika Prime arrives next week.", lex
        )
    }
    assert found == {"sphatika_prime_set"}


def test_the_cosmetic_noun_lookahead_does_not_cross_a_sentence_boundary():
    # Regression: "Citrine Prime." is a clean, standalone sentence. A stop-word
    # starting the NEXT sentence must not suppress synthesis just because it falls
    # within the two-token lookahead window.
    lex = build_lexicon([Item(slug="rage", name="Rage", url_name="a")])
    found = {c.slug for c in find_candidates("Citrine Prime. Bundle deals available soon.", lex)}
    assert found == {"citrine_prime_set"}


def test_the_adjacency_guard_alone_blocks_a_pack_list():
    lex = build_lexicon([Item(slug="rage", name="Rage", url_name="a")])
    found = find_candidates(
        "Select from the Weapons, Prime, Complete and Accessories Packs.", lex
    )
    assert found == []
