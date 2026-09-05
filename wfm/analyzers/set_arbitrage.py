from __future__ import annotations

from datetime import timedelta

from wfm.analyzers.base import Context
from wfm.features.types import FeatureSet
from wfm.models import Direction, Horizon, Scope, Signal

DEFAULTS = {
    "min_margin_plat": 10,
    "min_margin_pct": 0.05,
    "expiry_minutes": 20,
}


class SetArbitrageAnalyzer:
    """Detects a mispriced Set: warframe.market sells bundled "Set" items (blueprint +
    parts) alongside the same parts individually, and nobody keeps the two markets in
    sync. Identifies the bundle by slug suffix (`_set`), not a schema role: how a group
    is curated (one Set + its parts) is what makes it "arbitrage-shaped".
    """

    name = "set_arbitrage"
    horizon = Horizon.URGENT
    scope = Scope.GROUP
    DEFAULTS = DEFAULTS

    def required_features(self) -> set[str]:
        return {"book"}

    def evaluate(self, fss: list[FeatureSet], ctx: Context) -> list[Signal]:
        set_members = [fs for fs in fss if fs.slug.endswith("_set")]
        if len(set_members) != 1:
            return []
        set_fs = set_members[0]
        parts = [fs for fs in fss if fs is not set_fs]
        if not parts or set_fs.book is None or any(p.book is None for p in parts):
            return []
        t = {**DEFAULTS, **ctx.thresholds_for(self.name)}

        signals: list[Signal] = []

        # Buy the set, part it out: profitable when the set is cheaper than the sum
        # of what its parts sell for.
        set_ask = set_fs.book.online_best_ask
        parts_bid = [p.book.online_best_bid for p in parts]
        if set_ask is not None and all(b is not None for b in parts_bid):
            margin = sum(parts_bid) - set_ask
            margin_pct = margin / set_ask if set_ask else 0.0
            if margin >= t["min_margin_plat"] and margin_pct >= t["min_margin_pct"]:
                signals.append(
                    self._signal(
                        set_fs, Direction.BUY, margin, margin_pct, t,
                        set_ask=set_ask, set_bid=set_fs.book.online_best_bid,
                        parts={
                            p.slug: {"ask": p.book.online_best_ask, "bid": p.book.online_best_bid}
                            for p in parts
                        },
                    )
                )

        # Buy the parts, sell as a set: profitable when the set commands a premium
        # over what it costs to assemble from parts.
        set_bid = set_fs.book.online_best_bid
        parts_ask = [p.book.online_best_ask for p in parts]
        if set_bid is not None and all(a is not None for a in parts_ask):
            margin = set_bid - sum(parts_ask)
            margin_pct = margin / sum(parts_ask) if sum(parts_ask) else 0.0
            if margin >= t["min_margin_plat"] and margin_pct >= t["min_margin_pct"]:
                signals.append(
                    self._signal(
                        set_fs, Direction.SELL, margin, margin_pct, t,
                        set_ask=set_fs.book.online_best_ask, set_bid=set_bid,
                        parts={
                            p.slug: {"ask": p.book.online_best_ask, "bid": p.book.online_best_bid}
                            for p in parts
                        },
                    )
                )

        return signals

    def _signal(
        self, set_fs: FeatureSet, direction: Direction, margin: float, margin_pct: float,
        t: dict, **evidence,
    ) -> Signal:
        # min_margin_pct=0 means any positive margin trivially clears the threshold
        # (already true, or _signal wouldn't be called): treat that as full confidence
        # rather than dividing by zero.
        if t["min_margin_pct"] == 0:
            confidence = 1.0
        else:
            confidence = min(1.0, margin_pct / (2 * t["min_margin_pct"]))
        return Signal(
            slug=set_fs.slug,
            rank=set_fs.rank,
            analyzer=self.name,
            ts=set_fs.ts,
            direction=direction,
            magnitude=round(margin, 2),
            confidence=round(confidence, 3),
            horizon=self.horizon,
            expires_at=set_fs.ts + timedelta(minutes=t["expiry_minutes"]),
            evidence={
                "margin_plat": round(margin, 2),
                "margin_pct": round(margin_pct, 4),
                **evidence,
            },
        )


ANALYZER = SetArbitrageAnalyzer()
