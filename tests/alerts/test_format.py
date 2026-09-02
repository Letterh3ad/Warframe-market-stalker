from datetime import datetime, timedelta, timezone

from wfm.alerts.format import render_digest, render_signal
from wfm.models import Direction, Horizon, Signal

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _flip(magnitude: float = 20.0, slug: str = "primed_continuity") -> Signal:
    return Signal(
        slug=slug, rank=10, analyzer="flip", ts=NOW, direction=Direction.BUY,
        magnitude=magnitude, confidence=0.82, horizon=Horizon.URGENT,
        expires_at=NOW + timedelta(minutes=20),
        evidence={"fair_value": 50.0, "online_best_ask": 30, "margin_plat": 20.0,
                  "margin_pct": 0.4, "median_volume_30d": 40},
    )


def _revert(magnitude: float = 2.4) -> Signal:
    return Signal(
        slug="mirage_prime_set", rank=0, analyzer="revert", ts=NOW, direction=Direction.BUY,
        magnitude=magnitude, confidence=0.8, horizon=Horizon.DAILY,
        evidence={"robust_z": -magnitude, "excess_return_7d": -0.12, "median_90d": 180.0},
    )


def test_a_rendered_signal_names_the_item_direction_and_analyzer():
    text = render_signal(_flip(), name="Primed Continuity")
    assert "Primed Continuity" in text
    assert "rank 10" in text
    assert "BUY" in text
    assert "flip" in text


def test_evidence_is_rendered_so_the_signal_is_auditable():
    text = render_signal(_flip())
    for token in ("fair_value", "50", "online_best_ask", "30", "margin_pct"):
        assert token in text


def test_the_leading_evidence_keys_are_analyzer_specific():
    flip_line = render_signal(_flip()).splitlines()[1]
    revert_line = render_signal(_revert()).splitlines()[1]
    assert "fair_value" in flip_line
    assert "robust_z" in revert_line


def test_an_expiry_is_shown_for_urgent_signals_only():
    assert "expires" in render_signal(_flip())
    assert "expires" not in render_signal(_revert())


def test_a_signal_with_unknown_evidence_keys_still_renders():
    signal = Signal(slug="x", rank=0, analyzer="custom", ts=NOW, direction=Direction.HOLD,
                    magnitude=0.0, confidence=0.0, evidence={"anything": 1})
    assert "anything" in render_signal(signal)


def test_a_digest_groups_by_analyzer():
    text = render_digest([_flip(), _revert(), _revert(magnitude=3.0)])
    assert text.index("revert") < text.index("flip") or text.index("flip") < text.index("revert")
    assert text.count("revert") >= 1
    assert "3 signals" in text


def test_a_digest_caps_the_list_and_counts_the_rest():
    signals = [_revert(magnitude=float(i)) for i in range(1, 25)]
    text = render_digest(signals, cap=5)
    assert "19 more" in text


def test_the_digest_shows_the_largest_magnitudes_first():
    text = render_digest([_revert(magnitude=1.0), _revert(magnitude=9.0)], cap=1)
    assert "9" in text.splitlines()[2]


def test_an_empty_digest_says_so():
    assert "no signals" in render_digest([]).lower()
