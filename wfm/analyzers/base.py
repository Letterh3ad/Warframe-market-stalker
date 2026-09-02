from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from wfm.features.types import FeatureSet
from wfm.models import Horizon, Scope, Signal


@dataclass(frozen=True)
class Holding:
    slug: str
    rank: int
    quantity: int
    avg_cost: float


@dataclass(frozen=True)
class Context:
    now: datetime
    holdings: dict[tuple[str, int], Holding] = field(default_factory=dict)
    watchlist: dict[tuple[str, int], dict] = field(default_factory=dict)
    thresholds: dict[str, dict] = field(default_factory=dict)

    def holding_for(self, slug: str, rank: int) -> Holding | None:
        return self.holdings.get((slug, rank))

    def thresholds_for(self, name: str) -> dict:
        return self.thresholds.get(name, {})


@runtime_checkable
class Analyzer(Protocol):
    name: str
    horizon: Horizon
    scope: Scope
    DEFAULTS: dict

    def required_features(self) -> set[str]: ...


class ItemAnalyzer(Analyzer, Protocol):
    def evaluate(self, fs: FeatureSet, ctx: Context) -> list[Signal]: ...


class GroupAnalyzer(Analyzer, Protocol):
    def evaluate(self, fss: list[FeatureSet], ctx: Context) -> list[Signal]: ...
