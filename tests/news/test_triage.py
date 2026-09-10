import pytest

from wfm.news.triage import SIGNAL_GROUPS, signal_strength, triage
from wfm.news.types import Candidate


def cand(slug, context, score=1.0):
    return Candidate(slug=slug, name=slug, score=score, context=context, start=0, end=1)


def test_a_bug_fix_line_carries_no_signal():
    # This is the shape the filter exists for: a 37KB update note is mostly these.
    assert signal_strength("Fixed Rage not applying to Operator damage.") == 0


def test_a_vaulting_sentence_carries_signal():
    assert signal_strength("Mesa Prime enters the Prime Vault on the 20th.") >= 1


def test_a_balance_change_carries_signal():
    assert signal_strength("Increased Rage's energy conversion from 20% to 35%.") >= 1


def test_a_drop_rate_change_carries_signal():
    assert signal_strength("Adjusted the drop chance of Archon Continuity.") >= 1


def test_signal_counts_distinct_groups_not_occurrences():
    # Three vault words is still one kind of evidence.
    assert signal_strength("vault vault unvault") == 1


def test_signal_is_case_insensitive():
    assert signal_strength("MESA PRIME ENTERS THE PRIME VAULT") >= 1


def test_zero_signal_candidates_are_never_classified():
    kept, skipped = triage([cand("rage", "Fixed Rage not applying.")], cap=25)
    assert kept == []
    assert skipped == 1


def test_survivors_sort_by_signal_then_score_then_slug():
    weak = cand("b_weak", "Mesa Prime leaves the vault.")
    strong = cand("a_strong", "Unvaulted: increased drop chance for the relic.")
    kept, _ = triage([weak, strong], cap=25)
    assert [c.slug for c in kept] == ["a_strong", "b_weak"]


def test_ties_break_on_score_then_slug_so_the_order_is_deterministic():
    a = cand("zzz", "Mesa Prime enters the vault.", score=1.0)
    b = cand("aaa", "Mesa Prime enters the vault.", score=1.0)
    c = cand("mmm", "Mesa Prime enters the vault.", score=0.9)
    kept, _ = triage([a, b, c], cap=25)
    assert [x.slug for x in kept] == ["aaa", "zzz", "mmm"]


def test_the_cap_truncates_and_reports_what_it_dropped():
    many = [cand(f"s{i:02d}", "Mesa Prime enters the vault.") for i in range(40)]
    kept, skipped = triage(many, cap=25)
    assert len(kept) == 25
    assert skipped == 15


def test_the_cap_drops_the_weakest_signal_first():
    strong = [cand(f"a{i}", "Unvaulted, with an increased drop chance.") for i in range(3)]
    weak = [cand(f"z{i}", "Mesa Prime returns to the vault.") for i in range(3)]
    kept, skipped = triage(strong + weak, cap=3)
    assert {c.slug for c in kept} == {"a0", "a1", "a2"}
    assert skipped == 3


def test_a_cap_of_zero_keeps_nothing_rather_than_everything():
    kept, skipped = triage([cand("a", "enters the vault")], cap=0)
    assert kept == [] and skipped == 1


def test_no_candidates_is_not_an_error():
    assert triage([], cap=25) == ([], 0)


@pytest.mark.parametrize("group", SIGNAL_GROUPS)
def test_every_group_has_at_least_one_keyword(group):
    assert SIGNAL_GROUPS[group]
