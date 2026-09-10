"""What a classifier is, and how its answer becomes a database row.

Deliberately not `classify(article) -> list[ExtractedEvent]`, which is what the
design doc's signature says: per-candidate prompting means one request is one
subject, so one call yields one label set. Turning labels into an ExtractedEvent is
a pure function of the labels and the two config maps, and a backend has no business
doing it — it would have to import wfm.config, which the purity test forbids.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from typing import Protocol

from wfm.news.dates import resolve_effective_at
from wfm.news.types import (
    ClassifyLabels,
    ClassifyRequest,
    EventType,
    ExtractedEvent,
    NewsDirection,
)


class ClassifierError(RuntimeError):
    """The backend could not answer. The article is marked failed and retried later."""


class Classifier(Protocol):
    name: str
    version: str

    async def classify(self, req: ClassifyRequest) -> ClassifyLabels: ...


def to_event(
    labels: ClassifyLabels,
    subject: str,
    published_at: datetime | None,
    strength_map: Mapping[str, float],
    confidence_map: Mapping[str, float],
    store_raw_json: bool = False,
) -> ExtractedEvent:
    """Coarse labels plus the config maps -> the row that gets stored.

    Raises ValueError on a label the maps do not cover. Defaulting to 0.0 would
    store a real event that is weightless forever and looks identical to a
    correctly-scored irrelevance.
    """
    if labels.strength not in strength_map:
        raise ValueError(f"strength {labels.strength!r} is not in the strength map")
    if labels.confidence not in confidence_map:
        raise ValueError(f"confidence {labels.confidence!r} is not in the confidence map")

    return ExtractedEvent(
        event_type=EventType(labels.event_type),
        subject_raw=subject,
        direction=NewsDirection(labels.direction),
        strength=strength_map[labels.strength],
        confidence=confidence_map[labels.confidence],
        rationale=labels.rationale,
        effective_at=resolve_effective_at(
            labels.timing, labels.date_text, published_at
        ),
        # The labels, not the mapped numbers: a mapping change has to stay auditable
        # against what the model actually said.
        raw_json=json.dumps(asdict(labels), sort_keys=True) if store_raw_json else None,
    )


class FakeClassifier:
    """Canned answers, so the suite never needs a model.

    `by_subject` covers the common test shape: one article whose candidates should
    classify differently from each other.
    """

    name = "fake"
    version = "1"

    def __init__(
        self,
        labels: ClassifyLabels | None = None,
        by_subject: dict[str, ClassifyLabels] | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self._labels = labels
        self._by_subject = by_subject or {}
        self._fail_with = fail_with
        self.calls: list[ClassifyRequest] = []

    async def classify(self, req: ClassifyRequest) -> ClassifyLabels:
        self.calls.append(req)
        if self._fail_with is not None:
            raise self._fail_with
        answer = self._by_subject.get(req.subject, self._labels)
        if answer is None:
            raise ClassifierError(f"no canned answer for {req.subject!r}")
        return answer
