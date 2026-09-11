"""The one output contract, shared verbatim by every classifier backend.

Ollama takes OUTPUT_SCHEMA["schema"] as its `format`, Claude takes OUTPUT_SCHEMA as
`output_config.format`. Both constrain decoding, so a parse failure is structurally
impossible rather than something to defend against. `decode` validates anyway: the
fake backend and the benchmark's hand-written gold set come through the same door,
and nothing constrains either of them.
"""

from __future__ import annotations

from wfm.news.types import ClassifyLabels, ClassifyRequest, EventType, NewsDirection

EVENT_TYPES = tuple(e.value for e in EventType)
DIRECTIONS = tuple(d.value for d in NewsDirection)
STRENGTHS = ("minor", "moderate", "major")
CONFIDENCES = ("low", "medium", "high")
TIMINGS = ("immediate", "dated", "unknown")

_ENUMS = {
    "event_type": EVENT_TYPES,
    "direction": DIRECTIONS,
    "strength": STRENGTHS,
    "confidence": CONFIDENCES,
    "timing": TIMINGS,
}

OUTPUT_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "event_type": {"type": "string", "enum": list(EVENT_TYPES)},
            "direction": {"type": "string", "enum": list(DIRECTIONS)},
            "strength": {"type": "string", "enum": list(STRENGTHS)},
            "confidence": {"type": "string", "enum": list(CONFIDENCES)},
            "timing": {"type": "string", "enum": list(TIMINGS)},
            # As written in the article. Never an ISO date: relative date arithmetic
            # is where small models fail hardest, and a wrong effective_at anchors
            # the whole decay curve.
            "date_text": {"type": ["string", "null"]},
            "rationale": {"type": ["string", "null"]},
        },
        "required": [
            "event_type",
            "direction",
            "strength",
            "confidence",
            "timing",
            "date_text",
            "rationale",
        ],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = """You label Warframe market news. You are given one item name and \
the sentences around it, and you classify that one item's situation.

event_type:
  vault_in          the item is entering the Prime Vault (supply is being cut off)
  vault_out         the item is leaving the vault / being unvaulted (supply floods in)
  prime_access      a Prime variant is being released or announced
  buff              the item was made stronger
  nerf              the item was made weaker
  rework            the item was substantially changed, better or worse unclear
  drop_rate_change  how often it drops changed
  new_content       it is newly added to the game
  other             none of the above, including bug fixes and passing mentions

direction: where you expect the item's trading price to move: up, down or unclear.
strength: how big the change is: minor, moderate or major.
confidence: how sure you are the text really says this: low, medium or high.
timing:
  immediate  it is already in effect
  dated      the text names a date or day
  unknown    no timing is given
date_text: the date exactly as the text writes it, or null. Never compute a date.
rationale: one short sentence quoting or paraphrasing what made you decide.

If the sentences merely mention the item without any event, answer event_type "other" \
with direction "unclear". Do not guess."""


def build_prompt(req: ClassifyRequest) -> str:
    published = (
        req.published_at.date().isoformat()
        if req.published_at is not None
        else "unknown"
    )
    return (
        f"Published: {published}\n"
        f"Subject: {req.subject}\n"
        f"Context: {req.context}\n\n"
        "Classify the subject."
    )


def decode(payload: dict) -> ClassifyLabels:
    """Validate a raw payload into labels, raising ValueError on anything unexpected."""
    # A JSON array answer would otherwise raise AttributeError from .get, which no
    # caller catches: one malformed answer would abort a whole backfill.
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object, got {type(payload).__name__}")
    for field_name, allowed in _ENUMS.items():
        value = payload.get(field_name)
        if value not in allowed:
            raise ValueError(f"{field_name}={value!r} is not one of {allowed}")
    for field_name in ("date_text", "rationale"):
        if field_name not in payload:
            raise ValueError(f"missing {field_name}")

    date_text = payload["date_text"] or None
    rationale = payload["rationale"] or None
    return ClassifyLabels(
        event_type=payload["event_type"],
        direction=payload["direction"],
        strength=payload["strength"],
        confidence=payload["confidence"],
        timing=payload["timing"],
        date_text=date_text.strip() if isinstance(date_text, str) else None,
        rationale=rationale.strip() if isinstance(rationale, str) else None,
    )
