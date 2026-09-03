from __future__ import annotations

import math
from dataclasses import dataclass

from wfm.config import Config


@dataclass(frozen=True)
class Weights:
    vol: float = 1.0
    liq: float = 0.5
    spread: float = 0.8
    pin: float = 1.5

    @classmethod
    def from_config(cls, config: Config) -> Weights:
        return cls(
            vol=config.w_vol, liq=config.w_liq, spread=config.w_spread, pin=config.w_pin
        )


@dataclass(frozen=True)
class ScoreInputs:
    volatility: float | None = None
    volume: float | None = None
    online_spread_pct: float | None = None
    pin_weight: float = 0.0


def score(inputs: ScoreInputs, weights: Weights) -> float:
    volatility = inputs.volatility or 0.0
    volume = inputs.volume or 0.0
    spread = inputs.online_spread_pct or 0.0
    return (
        weights.vol * volatility
        + weights.liq * math.log1p(volume) / 10.0
        + weights.spread * spread
        + weights.pin * inputs.pin_weight
    )


def interval_minutes(
    value: float, floor: int = 30, ceiling: int = 2, saturation: float = 1.0
) -> float:
    fraction = min(1.0, max(0.0, value / saturation)) if saturation > 0 else 0.0
    return floor - fraction * (floor - ceiling)
