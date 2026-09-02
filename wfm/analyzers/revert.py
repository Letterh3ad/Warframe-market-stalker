from __future__ import annotations

from datetime import timedelta

from wfm.analyzers.base import Context
from wfm.features.types import FeatureSet
from wfm.models import Direction, Horizon, Scope, Signal

DEFAULTS = {
    "z_threshold": 1.5,
    "min_excess_return": 0.02,
    "min_volume_trend": 0.7,
    "expiry_hours": 48,
}


class RevertAnalyzer:
    name = "revert"
    horizon = Horizon.DAILY
    scope = Scope.ITEM
    DEFAULTS = DEFAULTS

    def required_features(self) -> set[str]:
        return {"price", "market"}

    def evaluate(self, fs: FeatureSet, ctx: Context) -> list[Signal]:
        if not fs.provenance.covers("price", "market") or fs.price is None or fs.market is None:
            return []
        t = {**DEFAULTS, **ctx.thresholds_for(self.name)}

        z = fs.price.robust_z
        excess = fs.market.excess_return_7d
        if z is None or excess is None or abs(z) < t["z_threshold"]:
            return []

        direction = Direction.BUY if z < 0 else Direction.SELL
        if direction is Direction.BUY:
            if excess > -t["min_excess_return"]:
                return []
            trend = fs.price.volume_trend
            # Price falling on collapsing volume means the item is dying, not cheap.
            if trend is not None and trend < t["min_volume_trend"]:
                return []
        elif excess < t["min_excess_return"]:
            return []

        return [
            Signal(
                slug=fs.slug,
                rank=fs.rank,
                analyzer=self.name,
                ts=fs.ts,
                direction=direction,
                magnitude=round(abs(z), 3),
                confidence=round(min(1.0, abs(z) / (2 * t["z_threshold"])), 3),
                horizon=self.horizon,
                expires_at=fs.ts + timedelta(hours=t["expiry_hours"]),
                evidence={
                    "robust_z": z,
                    "median_90d": fs.price.median_90d,
                    "last_close": fs.price.last_close,
                    "percentile_90d": fs.price.percentile_90d,
                    "volume_trend": fs.price.volume_trend,
                    "excess_return_7d": excess,
                    "tag": fs.market.tag,
                    "tag_median_return_7d": fs.market.tag_median_return_7d,
                    "market_median_return_7d": fs.market.market_median_return_7d,
                    "cohort_size": fs.market.cohort_size,
                },
            )
        ]


ANALYZER = RevertAnalyzer()
