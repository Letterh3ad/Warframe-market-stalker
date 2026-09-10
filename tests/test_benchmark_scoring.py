import importlib.util
from pathlib import Path

from wfm.news.types import ClassifyLabels

SPEC = importlib.util.spec_from_file_location(
    "news_benchmark", Path(__file__).resolve().parents[1] / "scripts" / "news_benchmark.py"
)
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


def labels(**kw):
    base = dict(
        event_type="vault_in", direction="up", strength="moderate",
        confidence="medium", timing="dated", date_text="September 20", rationale=None,
    )
    base.update(kw)
    return ClassifyLabels(**base)


def test_a_perfect_match_scores_one_on_every_field():
    got = bench.score({"a": labels()}, {"a": labels()})
    assert got["event_type"] == 1.0
    assert got["direction"] == 1.0
    assert got["strength"] == 1.0
    assert got["n"] == 1


def test_event_type_and_direction_need_an_exact_match():
    got = bench.score({"a": labels()}, {"a": labels(event_type="vault_out")})
    assert got["event_type"] == 0.0


def test_strength_and_confidence_score_within_one_label():
    # Ordinal scales: moderate-for-major is a near miss, minor-for-major is not.
    near = bench.score({"a": labels(strength="major")}, {"a": labels(strength="moderate")})
    far = bench.score({"a": labels(strength="major")}, {"a": labels(strength="minor")})
    assert near["strength"] == 1.0
    assert far["strength"] == 0.0


def test_effective_at_scores_within_a_day():
    same = bench.score({"a": labels()}, {"a": labels(date_text="September 20th")})
    off = bench.score({"a": labels()}, {"a": labels(date_text="October 20")})
    assert same["effective_at"] == 1.0
    assert off["effective_at"] == 0.0


def test_two_missing_dates_agree():
    got = bench.score(
        {"a": labels(timing="unknown", date_text=None)},
        {"a": labels(timing="unknown", date_text=None)},
    )
    assert got["effective_at"] == 1.0


def test_a_candidate_the_backend_never_answered_scores_zero_not_skipped():
    got = bench.score({"a": labels(), "b": labels()}, {"a": labels()})
    assert got["n"] == 2
    assert got["event_type"] == 0.5
    assert got["missing"] == 1
