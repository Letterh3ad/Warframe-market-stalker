from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

BLOCKS = ("price", "book", "seasonality", "market")


@dataclass(frozen=True)
class Provenance:
    """Availability is tracked here rather than inferred from `is None`, because a block
    can be present with individual fields missing. An analyzer needs to tell "no book was
    fetched" apart from "the book was fetched and has no online sellers".
    """

    samples: dict[str, int] = field(default_factory=dict)
    available: frozenset[str] = frozenset()

    @property
    def missing(self) -> frozenset[str]:
        return frozenset(BLOCKS) - self.available

    def covers(self, *names: str) -> bool:
        return all(name in self.available for name in names)


@dataclass(frozen=True)
class PriceFeatures:
    median_7d: float | None = None
    median_30d: float | None = None
    median_90d: float | None = None
    mad_90d: float | None = None
    robust_z: float | None = None
    percentile_90d: float | None = None
    atr_14d: float | None = None
    atr_pct: float | None = None
    volume_trend: float | None = None
    median_volume_30d: float | None = None
    donchian_position: float | None = None
    last_close: float | None = None


@dataclass(frozen=True)
class BookFeatures:
    best_bid: int | None = None
    best_ask: int | None = None
    online_best_bid: int | None = None
    online_best_ask: int | None = None
    spread: int | None = None
    online_spread: int | None = None
    spread_pct: float | None = None
    online_spread_pct: float | None = None
    bid_depth: tuple[int, ...] = ()
    ask_depth: tuple[int, ...] = ()
    imbalance: float | None = None
    stale_share: float | None = None
    bid_count: int = 0
    ask_count: int = 0
    online_bid_count: int = 0
    online_ask_count: int = 0


@dataclass(frozen=True)
class SeasonalityFeatures:
    bucket: int | None = None
    expected_volume: float | None = None
    expected_price: float | None = None
    volume_deviation: float | None = None
    price_deviation: float | None = None
    confidence: float = 0.0
    best_bucket_next_48h: int | None = None


@dataclass(frozen=True)
class MarketFeatures:
    market_median_return_7d: float | None = None
    tag: str | None = None
    tag_median_return_7d: float | None = None
    excess_return_7d: float | None = None
    cohort_size: int = 0


@dataclass(frozen=True)
class FeatureSet:
    slug: str
    rank: int
    ts: datetime
    price: PriceFeatures | None = None
    book: BookFeatures | None = None
    seasonality: SeasonalityFeatures | None = None
    market: MarketFeatures | None = None
    provenance: Provenance = field(default_factory=Provenance)

    def has(self, name: str) -> bool:
        return name in self.provenance.available

    def to_dict(self) -> dict:
        payload: dict = {"slug": self.slug, "rank": self.rank, "ts": self.ts.isoformat()}
        for name in BLOCKS:
            block = getattr(self, name)
            payload[name] = asdict(block) if block is not None else None
        payload["provenance"] = {
            "samples": dict(self.provenance.samples),
            "available": sorted(self.provenance.available),
            "missing": sorted(self.provenance.missing),
        }
        return payload
