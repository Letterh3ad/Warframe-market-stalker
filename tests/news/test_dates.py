from datetime import datetime, timezone

import pytest

from wfm.news.dates import resolve_effective_at

PUB = datetime(2026, 9, 6, 14, 30, tzinfo=timezone.utc)


def test_immediate_anchors_on_the_publish_date():
    assert resolve_effective_at("immediate", None, PUB) == PUB


def test_immediate_with_no_publish_date_is_none():
    assert resolve_effective_at("immediate", None, None) is None


def test_unknown_timing_is_none_even_with_a_date():
    assert resolve_effective_at("unknown", "September 20", PUB) is None


@pytest.mark.parametrize(
    "text",
    [
        "September 20",
        "September 20th",
        "Sept 20",
        "Sep. 20",
        "20 September",
        "20th September",
    ],
)
def test_a_month_and_day_resolve_to_utc_midnight(text):
    assert resolve_effective_at("dated", text, PUB) == datetime(
        2026, 9, 20, tzinfo=timezone.utc
    )


def test_an_explicit_year_wins():
    assert resolve_effective_at("dated", "September 20, 2027", PUB) == datetime(
        2027, 9, 20, tzinfo=timezone.utc
    )


def test_an_iso_date_parses():
    assert resolve_effective_at("dated", "2026-09-20", PUB) == datetime(
        2026, 9, 20, tzinfo=timezone.utc
    )


def test_a_date_that_would_land_in_the_past_rolls_to_next_year():
    pub = datetime(2026, 12, 28, tzinfo=timezone.utc)
    assert resolve_effective_at("dated", "January 5", pub) == datetime(
        2027, 1, 5, tzinfo=timezone.utc
    )


def test_a_date_just_behind_publication_stays_in_the_publish_year():
    # A hotfix published on the 6th describing something effective on the 1st is
    # backdated, not thirteen months out.
    assert resolve_effective_at("dated", "September 1", PUB) == datetime(
        2026, 9, 1, tzinfo=timezone.utc
    )


@pytest.mark.parametrize(
    "text",
    ["next Tuesday", "with the next mainline", "soon", "TBD", "", "the 20th", "Smarch 40"],
)
def test_a_relative_or_impossible_date_is_none(text):
    # None is the safe answer: the decay anchor falls back to published_at, which
    # active_links already COALESCEs. A wrong date poisons the bias instead.
    assert resolve_effective_at("dated", text, PUB) is None


def test_dated_without_a_publish_date_still_parses_an_explicit_year():
    assert resolve_effective_at("dated", "September 20, 2026", None) == datetime(
        2026, 9, 20, tzinfo=timezone.utc
    )


def test_dated_without_a_publish_date_cannot_infer_a_year():
    assert resolve_effective_at("dated", "September 20", None) is None


def test_immediate_with_naive_publish_date_raises_value_error():
    naive = datetime(2026, 9, 6, 14, 30)
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_effective_at("immediate", None, naive)


def test_dated_with_naive_publish_date_raises_value_error():
    naive = datetime(2026, 9, 6, 14, 30)
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_effective_at("dated", "September 20", naive)


def test_a_non_month_word_before_the_date_does_not_abandon_the_parse():
    # "Update 43" matches the month-first shape first; stopping at it lost the real
    # date behind it, which is how a dated event silently became undated.
    assert resolve_effective_at(
        "dated", "on Update 43 the vault opens September 20", PUB
    ) == datetime(2026, 9, 20, tzinfo=timezone.utc)


def test_a_non_month_word_before_a_day_first_date_does_not_abandon_the_parse():
    assert resolve_effective_at(
        "dated", "Hotfix 43 lands, vault opens 20 September", PUB
    ) == datetime(2026, 9, 20, tzinfo=timezone.utc)
