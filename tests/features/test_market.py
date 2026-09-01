import statistics

import pytest

from wfm.features.market import build, build_context, returns_over
from wfm.models import DailyCandle


def _series(slug: str, closes: list[float]) -> list[DailyCandle]:
    return [
        DailyCandle(slug=slug, rank=0, date=f"2026-08-{i + 1:02d}", close=c, median=c, volume=5)
        for i, c in enumerate(closes)
    ]


FLAT = _series("flat", [40] * 8)
UP = _series("up", [40, 41, 42, 43, 44, 45, 46, 48])
DOWN = _series("down", [50, 49, 48, 47, 46, 45, 44, 40])

TAGS = {"flat": ("mod",), "up": ("mod",), "down": ("set",)}
SERIES = {"flat": FLAT, "up": UP, "down": DOWN}


def test_returns_over_is_the_relative_change_across_the_window():
    # 8 closes span 7 daily intervals, so the window opens at closes[-8] == 40.
    assert returns_over(UP, days=7) == pytest.approx((48 - 40) / 40)
    assert returns_over(FLAT, days=7) == 0.0


def test_returns_over_spans_exactly_the_requested_number_of_days():
    # 3 closes, 2 intervals: a 2 day return must open at the first close, not the second.
    assert returns_over(_series("x", [10, 11, 20]), days=2) == pytest.approx(1.0)


def test_returns_over_needs_the_full_window():
    assert returns_over(_series("x", [40, 41]), days=7) is None


def test_context_reports_the_market_median_and_per_tag_medians():
    context = build_context(SERIES, TAGS, days=7)
    assert context.median_return == pytest.approx(returns_over(FLAT, 7))
    assert context.tag_returns["mod"] == pytest.approx(
        (returns_over(FLAT, 7) + returns_over(UP, 7)) / 2
    )
    assert context.cohort_sizes["set"] == 1


def test_excess_return_separates_an_item_from_its_cohort():
    context = build_context(SERIES, TAGS, days=7)
    features, samples = build(UP, tags=("mod",), context=context, slug="up")
    assert features.tag == "mod"
    # the cohort is its mod peers with up itself removed, which is flat alone
    assert features.excess_return_7d == pytest.approx(
        returns_over(UP, 7) - returns_over(FLAT, 7)
    )
    assert features.cohort_size == 1
    assert samples["market_cohort"] == 1


def test_an_item_whose_tag_has_no_cohort_still_gets_an_excess_against_the_market():
    context = build_context(SERIES, TAGS, days=7)
    features, _ = build(UP, tags=("unknown_tag",), context=context, slug="up")
    assert features.cohort_size == 0
    assert features.excess_return_7d is not None


def test_a_thin_item_has_no_excess_return_but_still_carries_the_market_read():
    context = build_context(SERIES, TAGS, days=7)
    features, _ = build(_series("thin", [40, 41]), tags=("mod",), context=context)
    assert features.excess_return_7d is None
    assert features.market_median_return_7d == context.median_return


def test_an_empty_market_yields_no_context_numbers():
    context = build_context({}, {}, days=7)
    assert context.median_return is None
    features, _ = build(UP, tags=("mod",), context=context)
    assert features.excess_return_7d is None


def test_a_zero_opening_price_does_not_divide_by_zero():
    assert returns_over(_series("x", [0, 1, 2]), days=2) is None


def _dated(slug: str, pairs: list[tuple[str, float]]) -> list[DailyCandle]:
    return [
        DailyCandle(slug=slug, rank=0, date=d, close=c, median=c, volume=5) for d, c in pairs
    ]


def test_a_seven_day_return_spans_seven_days_not_eight_data_points():
    """The API omits untraded days, so an illiquid item's 8 newest closes can span
    months. Reporting that as a 7 day return invents a move that never happened.
    """
    sparse = _dated("s", [("2026-06-01", 10), ("2026-06-05", 11), ("2026-06-12", 12),
                          ("2026-07-01", 13), ("2026-07-15", 14), ("2026-08-01", 15),
                          ("2026-08-10", 16), ("2026-08-28", 20)])
    assert returns_over(sparse, days=7) is None


def test_an_item_is_not_compared_against_itself():
    """A market of one is still the item itself. Removing it from the tag cohort but
    leaving it in the market median gives a benchmark equal to its own return, so
    excess_return comes back a confident 0.0 that means nothing.
    """
    context = build_context({"up": UP}, {"up": ("solo",)}, days=7)
    features, _ = build(UP, tags=("solo",), context=context, slug="up")
    assert features.cohort_size == 0
    assert features.tag_median_return_7d is None
    assert features.excess_return_7d is None, "there is nothing to compare against"


def test_the_market_fallback_also_excludes_the_item():
    context = build_context(SERIES, TAGS, days=7)
    features, _ = build(UP, tags=("unknown_tag",), context=context, slug="up")
    peers_only = statistics.median([returns_over(FLAT, 7), returns_over(DOWN, 7)])
    assert features.excess_return_7d == pytest.approx(returns_over(UP, 7) - peers_only)


def test_a_cohort_excludes_the_item_being_measured():
    context = build_context(SERIES, TAGS, days=7)
    features, _ = build(UP, tags=("mod",), context=context, slug="up")
    assert features.cohort_size == 1, "flat is the only mod peer once up is excluded"
    assert features.tag_median_return_7d == pytest.approx(returns_over(FLAT, 7))


def test_a_missing_cohort_does_not_disguise_the_market_number_as_a_tag_number():
    context = build_context(SERIES, TAGS, days=7)
    features, _ = build(UP, tags=("unknown_tag",), context=context, slug="up")
    assert features.tag_median_return_7d is None
    assert features.market_median_return_7d == context.median_return
