from __future__ import annotations

import logging

from wfm.analyzers.base import Context
from wfm.features.types import FeatureSet
from wfm.models import Scope, Signal

log = logging.getLogger(__name__)


def run_item(analyzers: list, fs: FeatureSet, ctx: Context) -> tuple[list[Signal], list[str]]:
    signals: list[Signal] = []
    skipped: list[str] = []
    for analyzer in analyzers:
        if analyzer.scope is not Scope.ITEM:
            continue
        if not fs.provenance.covers(*analyzer.required_features()):
            skipped.append(analyzer.name)
            continue
        try:
            signals.extend(analyzer.evaluate(fs, ctx))
        except Exception:
            log.exception("analyzer %s failed on %s", analyzer.name, fs.slug)
            skipped.append(analyzer.name)
    return signals, skipped


def run_group(
    analyzers: list, feature_sets: list[FeatureSet], ctx: Context
) -> tuple[list[Signal], list[str]]:
    signals: list[Signal] = []
    skipped: list[str] = []
    for analyzer in analyzers:
        if analyzer.scope is not Scope.GROUP:
            continue
        required = analyzer.required_features()
        if not all(fs.provenance.covers(*required) for fs in feature_sets):
            skipped.append(analyzer.name)
            continue
        try:
            signals.extend(analyzer.evaluate(feature_sets, ctx))
        except Exception:
            log.exception("group analyzer %s failed", analyzer.name)
            skipped.append(analyzer.name)
    return signals, skipped
