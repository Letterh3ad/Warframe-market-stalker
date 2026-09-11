from datetime import datetime, timezone

import pytest

from wfm.news import schema
from wfm.news.types import ClassifyRequest, EventType


def req(**kw):
    base = dict(
        subject="Mesa Prime",
        context="Mesa Prime enters the Prime Vault on September 20th.",
        published_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
    )
    base.update(kw)
    return ClassifyRequest(**base)


def test_the_schema_enumerates_every_event_type():
    props = schema.OUTPUT_SCHEMA["schema"]["properties"]
    assert set(props["event_type"]["enum"]) == {e.value for e in EventType}


def test_the_schema_forbids_extra_keys_and_requires_every_field():
    body = schema.OUTPUT_SCHEMA["schema"]
    assert body["additionalProperties"] is False
    assert set(body["required"]) == set(body["properties"])


def test_the_schema_has_no_numeric_field():
    # The model never emits a number: strength and confidence are labels that code
    # maps through config, so the backtest can retune without reclassifying.
    for name, spec in schema.OUTPUT_SCHEMA["schema"]["properties"].items():
        assert spec.get("type") not in ("number", "integer"), name


def test_the_prompt_carries_the_subject_the_context_and_the_publish_date():
    text = schema.build_prompt(req())
    assert "Mesa Prime" in text
    assert "Prime Vault" in text
    assert "2026-09-06" in text


def test_the_prompt_survives_a_missing_publish_date():
    assert "unknown" in schema.build_prompt(req(published_at=None)).lower()


def test_decode_accepts_a_well_formed_payload():
    labels = schema.decode(
        {
            "event_type": "vault_in",
            "direction": "up",
            "strength": "major",
            "confidence": "high",
            "timing": "dated",
            "date_text": "September 20",
            "rationale": "Entering the vault cuts supply.",
        }
    )
    assert labels.event_type == "vault_in"
    assert labels.date_text == "September 20"


def test_decode_treats_an_empty_date_as_absent():
    labels = schema.decode(
        {
            "event_type": "buff",
            "direction": "up",
            "strength": "minor",
            "confidence": "low",
            "timing": "immediate",
            "date_text": "",
            "rationale": None,
        }
    )
    assert labels.date_text is None


@pytest.mark.parametrize(
    "bad",
    [
        {"event_type": "vaulting"},  # not in the enum
        {"direction": "sideways"},
        {"strength": "0.6"},  # a number wearing a label's clothes
        {"confidence": "very high"},
        {"timing": "later"},
    ],
)
def test_decode_rejects_an_out_of_enum_value(bad):
    payload = {
        "event_type": "buff",
        "direction": "up",
        "strength": "minor",
        "confidence": "low",
        "timing": "unknown",
        "date_text": None,
        "rationale": None,
    }
    payload.update(bad)
    with pytest.raises(ValueError):
        schema.decode(payload)


def test_decode_rejects_a_missing_field():
    with pytest.raises(ValueError):
        schema.decode({"event_type": "buff"})


def test_decode_rejects_a_non_object_payload_as_a_value_error():
    # A JSON array would reach .get and raise AttributeError, which neither backend
    # nor the classify loop catches: one bad answer would abort the whole run.
    for payload in ([], "ok", 3, None):
        with pytest.raises(ValueError):
            schema.decode(payload)
