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
