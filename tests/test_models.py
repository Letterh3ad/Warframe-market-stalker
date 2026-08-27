import dataclasses
from datetime import datetime, timezone

import pytest

from wfm.models import DailyCandle, Direction, Horizon, Item, Signal


def test_item_defaults_to_rankless():
    item = Item(slug="mirage_prime_set", name="Mirage Prime Set", url_name="mirage_prime_set")
    assert item.max_rank == 0
    assert item.canonical_rank == 0
    assert item.tags == ()


def test_models_are_frozen():
    candle = DailyCandle(slug="x", rank=0, date="2026-08-01", volume=3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        candle.volume = 9


def test_signal_evidence_is_a_plain_dict():
    signal = Signal(
        slug="x",
        rank=0,
        analyzer="flip",
        ts=datetime(2026, 8, 27, tzinfo=timezone.utc),
        direction=Direction.BUY,
        magnitude=12.5,
        confidence=0.8,
        evidence={"online_best_ask": 40, "fair_value": 52.5},
        horizon=Horizon.URGENT,
    )
    assert signal.evidence["fair_value"] == 52.5
    assert signal.id is None
    assert signal.alerted_at is None
