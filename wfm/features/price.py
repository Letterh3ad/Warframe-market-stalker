from __future__ import annotations

import statistics

from wfm.features.types import PriceFeatures
from wfm.models import DailyCandle

MAD_TO_SD = 0.6745
"""Scale factor making MAD comparable to a standard deviation for normal data."""


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def mad(values: list[float]) -> float | None:
    center = median(values)
    if center is None:
        return None
    return statistics.median([abs(v - center) for v in values])


def robust_z(value: float, values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    center = median(values)
    spread = mad(values)
    if not spread:
        return None
    return MAD_TO_SD * (value - center) / spread


def percentile_rank(value: float, values: list[float]) -> float | None:
    if not values:
        return None
    return sum(1 for v in values if v <= value) / len(values)


def atr(candles: list[DailyCandle], window: int = 14) -> float | None:
    usable = [
        c for c in candles if c.high is not None and c.low is not None and c.close is not None
    ]
    if len(usable) < 2:
        return None
    ranges: list[float] = []
    for previous, current in zip(usable, usable[1:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    recent = ranges[-window:]
    return sum(recent) / len(recent)


def volume_trend(candles: list[DailyCandle], short: int = 7, long: int = 30) -> float | None:
    volumes = [c.volume for c in candles if c.volume is not None]
    if len(volumes) < long:
        return None
    long_median = median(volumes[-long:])
    short_median = median(volumes[-short:])
    if not long_median:
        return None
    return short_median / long_median


def donchian_position(candles: list[DailyCandle]) -> float | None:
    if not candles or candles[-1].close is None:
        return None
    last = candles[-1]
    top, bottom = last.donch_top, last.donch_bot
    if top is None or bottom is None:
        highs = [c.high for c in candles if c.high is not None]
        lows = [c.low for c in candles if c.low is not None]
        if not highs or not lows:
            return None
        top, bottom = max(highs), min(lows)
    if top - bottom <= 0:
        return None
    return (last.close - bottom) / (top - bottom)


def build(candles: list[DailyCandle]) -> tuple[PriceFeatures, dict[str, int]]:
    """A window statistic stays None unless its window is fully populated. A "30 day
    median" taken from four points is exactly the plausible-looking wrong number this
    layer exists to avoid.
    """
    closes = [c.close for c in candles if c.close is not None]
    volumes = [c.volume for c in candles if c.volume is not None]
    samples = {
        "price_90d": len(candles),
        "price_30d": min(len(closes), 30),
        "price_7d": min(len(closes), 7),
        "volume_30d": min(len(volumes), 30),
    }
    if not closes:
        return PriceFeatures(), samples

    last_close = closes[-1]
    window_90 = closes[-90:] if len(closes) >= 90 else None
    window_30 = closes[-30:] if len(closes) >= 30 else None
    window_7 = closes[-7:] if len(closes) >= 7 else None
    atr_value = atr(candles)

    return (
        PriceFeatures(
            median_7d=median(window_7) if window_7 else None,
            median_30d=median(window_30) if window_30 else None,
            median_90d=median(window_90) if window_90 else None,
            mad_90d=mad(window_90) if window_90 else None,
            robust_z=robust_z(last_close, window_90) if window_90 else None,
            percentile_90d=percentile_rank(last_close, window_90) if window_90 else None,
            atr_14d=atr_value,
            atr_pct=(atr_value / last_close) if atr_value and last_close else None,
            volume_trend=volume_trend(candles),
            median_volume_30d=median(volumes[-30:]) if len(volumes) >= 30 else None,
            donchian_position=donchian_position(candles),
            last_close=last_close,
        ),
        samples,
    )
