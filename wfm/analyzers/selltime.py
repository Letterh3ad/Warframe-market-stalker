from __future__ import annotations

from datetime import timedelta

from wfm.analyzers.base import Context
from wfm.features.types import FeatureSet
from wfm.models import Direction, Horizon, Scope, Signal

DEFAULTS = {
    "sell_percentile": 0.8,
    "hold_percentile": 0.35,
    "min_seasonality_confidence": 0.75,
    "min_bid_depth_ratio": 1.0,
    "expiry_hours": 24,
}


class SellTimeAnalyzer:
    name = "selltime"
    horizon = Horizon.DAILY
    scope = Scope.ITEM
    DEFAULTS = DEFAULTS

    def required_features(self) -> set[str]:
        return {"price"}

    def evaluate(self, fs: FeatureSet, ctx: Context) -> list[Signal]:
        holding = ctx.holding_for(fs.slug, fs.rank)
        if holding is None or holding.quantity <= 0:
            return []
        if not fs.provenance.covers("price") or fs.price is None:
            return []
        t = {**DEFAULTS, **ctx.thresholds_for(self.name)}

        percentile = fs.price.percentile_90d
        if percentile is None:
            return []

        reference = (
            fs.book.online_best_bid
            if fs.book is not None and fs.book.online_best_bid is not None
            else fs.price.last_close
        )
        # Total online bid quantity across the visible levels: the last cumulative entry.
        online_depth = (
            fs.book.online_bid_depth[-1]
            if fs.book is not None and fs.book.online_bid_depth
            else None
        )
        depth_ok = (
            online_depth is None
            or online_depth >= holding.quantity * t["min_bid_depth_ratio"]
        )

        seasonality_confidence = fs.seasonality.confidence if fs.seasonality else 0.0
        seasonality_used = seasonality_confidence >= t["min_seasonality_confidence"]
        wait_for = (
            fs.seasonality.best_bucket_next_48h
            if seasonality_used and fs.seasonality is not None
            else None
        )

        if percentile >= t["sell_percentile"] and depth_ok:
            direction, recommendation, reason = Direction.SELL, "list now", "percentile"
        elif percentile >= t["sell_percentile"]:
            direction, recommendation, reason = (
                Direction.HOLD,
                "hold",
                "price is high but online bid depth cannot absorb the position",
            )
        elif wait_for is not None:
            direction, recommendation, reason = Direction.HOLD, "wait", "better window ahead"
        else:
            direction, recommendation, reason = Direction.HOLD, "hold", "percentile"

        pnl = (
            round((reference - holding.avg_cost) * holding.quantity, 2)
            if reference is not None
            else None
        )
        return [
            Signal(
                slug=fs.slug,
                rank=fs.rank,
                analyzer=self.name,
                ts=fs.ts,
                direction=direction,
                magnitude=round(abs(percentile - t["sell_percentile"]), 3),
                confidence=round(min(1.0, percentile), 3),
                horizon=self.horizon,
                expires_at=fs.ts + timedelta(hours=t["expiry_hours"]),
                evidence={
                    "recommendation": recommendation,
                    "reason": reason,
                    "percentile_90d": percentile,
                    "median_90d": fs.price.median_90d,
                    "reference_price": reference,
                    "quantity_held": holding.quantity,
                    "avg_cost": holding.avg_cost,
                    "unrealized_pnl": pnl,
                    "online_bid_depth": online_depth,
                    "seasonality_used": seasonality_used,
                    "seasonality_confidence": seasonality_confidence,
                    "wait_for_bucket": wait_for,
                },
            )
        ]


ANALYZER = SellTimeAnalyzer()
