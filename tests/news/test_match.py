from wfm.models import Item
from wfm.news.match import build_lexicon, normalize

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
