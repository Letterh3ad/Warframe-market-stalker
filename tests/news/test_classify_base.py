import json
from datetime import datetime, timezone

import pytest

from wfm.news.classify.base import ClassifierError, FakeClassifier, to_event
from wfm.news.types import (
    ClassifyLabels,
    ClassifyRequest,
    EventType,
    NewsDirection,
)

PUB = datetime(2026, 9, 6, tzinfo=timezone.utc)
STRENGTH = {"minor": 0.3, "moderate": 0.6, "major": 0.9}
CONFIDENCE = {"low": 0.4, "medium": 0.7, "high": 0.95}


def labels(**kw):
    base = dict(
        event_type="vault_in",
        direction="up",
        strength="major",
        confidence="high",
        timing="dated",
        date_text="September 20",
        rationale="Entering the vault cuts supply.",
    )
    base.update(kw)
    return ClassifyLabels(**base)


def test_labels_map_to_the_configured_numbers():
    event = to_event(labels(), "Mesa Prime", PUB, STRENGTH, CONFIDENCE)
    assert event.event_type is EventType.VAULT_IN
    assert event.direction is NewsDirection.UP
    assert event.strength == 0.9
    assert event.confidence == 0.95
    assert event.subject_raw == "Mesa Prime"


def test_retuning_the_maps_changes_the_numbers_without_touching_the_labels():
    event = to_event(labels(), "Mesa Prime", PUB, {"major": 0.5}, {"high": 0.5})
    assert event.strength == 0.5 and event.confidence == 0.5


def test_an_unmapped_label_raises_rather_than_defaulting():
    # A silent 0.0 would look like a real, weightless event forever.
    with pytest.raises(ValueError):
        to_event(labels(strength="enormous"), "X", PUB, STRENGTH, CONFIDENCE)


def test_a_dated_event_resolves_effective_at():
    event = to_event(labels(), "Mesa Prime", PUB, STRENGTH, CONFIDENCE)
    assert event.effective_at == datetime(2026, 9, 20, tzinfo=timezone.utc)


def test_an_unparseable_date_leaves_effective_at_null():
    event = to_event(
        labels(date_text="with the next mainline"), "X", PUB, STRENGTH, CONFIDENCE
    )
    assert event.effective_at is None


def test_raw_json_is_absent_unless_asked_for():
    assert to_event(labels(), "X", PUB, STRENGTH, CONFIDENCE).raw_json is None


def test_raw_json_records_exactly_what_the_model_said():
    event = to_event(labels(), "X", PUB, STRENGTH, CONFIDENCE, store_raw_json=True)
    assert json.loads(event.raw_json)["strength"] == "major"
    assert "0.9" not in event.raw_json  # labels, not the mapped numbers


def test_the_rationale_survives():
    event = to_event(labels(), "X", PUB, STRENGTH, CONFIDENCE)
    assert event.rationale == "Entering the vault cuts supply."


async def test_the_fake_returns_its_canned_labels_and_records_the_request():
    fake = FakeClassifier(labels(event_type="nerf"))
    req = ClassifyRequest(subject="Rage", context="Rage was reduced.", published_at=PUB)
    got = await fake.classify(req)
    assert got.event_type == "nerf"
    assert fake.calls == [req]


async def test_the_fake_can_answer_per_subject():
    fake = FakeClassifier(
        labels(), by_subject={"Rage": labels(event_type="nerf", direction="down")}
    )
    assert (await fake.classify(ClassifyRequest("Rage", "c", PUB))).event_type == "nerf"
    assert (await fake.classify(ClassifyRequest("Mesa Prime", "c", PUB))).event_type == (
        "vault_in"
    )


async def test_the_fake_can_simulate_an_unreachable_backend():
    fake = FakeClassifier(labels(), fail_with=ClassifierError("ollama is down"))
    with pytest.raises(ClassifierError):
        await fake.classify(ClassifyRequest("X", "c", PUB))
