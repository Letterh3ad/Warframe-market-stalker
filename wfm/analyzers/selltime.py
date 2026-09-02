from __future__ import annotations

from wfm.analyzers.base import Context
from wfm.features.types import FeatureSet
from wfm.models import Horizon, Scope, Signal

DEFAULTS: dict = {}


class SellTimeAnalyzer:
    name = "selltime"
    horizon = Horizon.DAILY
    scope = Scope.ITEM
    DEFAULTS = DEFAULTS

    def required_features(self) -> set[str]:
        return {"price"}

    def evaluate(self, fs: FeatureSet, ctx: Context) -> list[Signal]:
        return []


ANALYZER = SellTimeAnalyzer()
