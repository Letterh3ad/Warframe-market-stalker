from __future__ import annotations

from datetime import timedelta

from wfm.analyzers.base import Context
from wfm.features.types import FeatureSet
from wfm.models import Direction, Horizon, Scope, Signal

DEFAULTS = {
    "min_margin_plat": 10,
    "min_margin_pct": 0.15,
    "min_daily_volume": 5,
    "max_stale_share": 0.6,
    "min_quantity_at_best": 1,
    "expiry_minutes": 20,
}


class FlipAnalyzer:
    name = "flip"
    horizon = Horizon.URGENT
    scope = Scope.ITEM
    DEFAULTS = DEFAULTS

    def required_features(self) -> set[str]:
        return {"price", "book"}

    def evaluate(self, fs: FeatureSet, ctx: Context) -> list[Signal]:
        if not fs.provenance.covers("price", "book") or fs.price is None or fs.book is None:
            return []
        t = {**DEFAULTS, **ctx.thresholds_for(self.name)}

        ask = fs.book.online_best_ask
        fair_value = fs.price.median_7d or fs.price.median_30d
        if ask is None or not fair_value:
            return []

        margin = fair_value - ask
        margin_pct = margin / fair_value
        if margin < t["min_margin_plat"] or margin_pct < t["min_margin_pct"]:
            return []

        volume = fs.price.median_volume_30d
        if volume is None or volume < t["min_daily_volume"]:
            return []
        if fs.book.stale_share is not None and fs.book.stale_share > t["max_stale_share"]:
            return []

        # Online depth only: an offline wall does not stop an online fill and must not
        # be counted as proof the price is real.
        quantity_at_best = fs.book.online_ask_depth[0] if fs.book.online_ask_depth else 0
        if quantity_at_best < t["min_quantity_at_best"]:
            return []

        confidence = min(1.0, margin_pct / (2 * t["min_margin_pct"])) * min(
            1.0, volume / (2 * t["min_daily_volume"])
        )
        bid = fs.book.online_best_bid
        return [
            Signal(
                slug=fs.slug,
                rank=fs.rank,
                analyzer=self.name,
                ts=fs.ts,
                direction=Direction.BUY,
                magnitude=round(margin, 2),
                confidence=round(confidence, 3),
                horizon=self.horizon,
                expires_at=fs.ts + timedelta(minutes=t["expiry_minutes"]),
                evidence={
                    "fair_value": fair_value,
                    "fair_value_source": "median_7d" if fs.price.median_7d else "median_30d",
                    "online_best_ask": ask,
                    "online_best_bid": bid,
                    "margin_plat": round(margin, 2),
                    "margin_pct": round(margin_pct, 4),
                    "crossed_book": bid is not None and ask < bid,
                    "quantity_at_best_ask": quantity_at_best,
                    "median_volume_30d": volume,
                    "stale_share": fs.book.stale_share,
                    "online_ask_count": fs.book.online_ask_count,
                },
            )
        ]


ANALYZER = FlipAnalyzer()
