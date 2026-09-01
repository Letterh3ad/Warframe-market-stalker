from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

from wfm.features.types import PriceFeatures
from wfm.models import DailyCandle

MAD_TO_SD = 0.6745
"""Scale factor making MAD comparable to a standard deviation for normal data."""

MIN_COVERAGE = 0.9
"""Share of a window's days that must carry a close before its statistics are computed.

Not 1.0: warframe.market publishes complete days only, so a 90 day series arrives as 89
candles and a strict rule would null every long window permanently. 0.9 still refuses the
thin history that produces plausible wrong signals, which is the point of the guard.
"""


def _day(candle: DailyCandle) -> date:
    return date.fromisoformat(candle.date)


def window(candles: list[DailyCandle], days: int) -> list[DailyCandle] | None:
    """The candles inside the last `days` calendar days, or None if too few are covered.

    Measured in calendar days rather than in data points: the API omits untraded days
    entirely, so an illiquid item can hold 27 closes spread over three months. Counting
    points would happily label that a 30 day median.
    """
    dated = [c for c in candles if c.close is not None]
    if not dated:
        return None
    end = _day(dated[-1])
    start = end - timedelta(days=days - 1)
    inside = [c for c in dated if start <= _day(c) <= end]
    return inside if len(inside) >= math.ceil(days * MIN_COVERAGE) else None


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
    long_window = window(candles, long)
    short_window = window(candles, short)
    if long_window is None or short_window is None:
        return None
    long_volumes = [c.volume for c in long_window if c.volume is not None]
    short_volumes = [c.volume for c in short_window if c.volume is not None]
    if not long_volumes or not short_volumes:
        return None
    long_median = median(long_volumes)
    if not long_median:
        return None
    return median(short_volumes) / long_median


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
    """A window statistic stays None unless the window clears MIN_COVERAGE. A "30 day
    median" taken from four points is exactly the plausible-looking wrong number this
    layer exists to avoid.
    """
    closes = [c.close for c in candles if c.close is not None]
    volumes = [c.volume for c in candles if c.volume is not None]
    # Counted over closes, the same values the coverage gate reads, so a null statistic
    # can always be explained by the sample count sitting next to it.
    samples = {
        "price_90d": len(closes),
        "price_30d": min(len(closes), 30),
        "price_7d": min(len(closes), 7),
        "volume_30d": min(len(volumes), 30),
    }
    if not closes:
        return PriceFeatures(), samples

    last_close = closes[-1]
    candles_90 = window(candles, 90)
    candles_30 = window(candles, 30)
    candles_7 = window(candles, 7)
    window_90 = [c.close for c in candles_90] if candles_90 else None
    window_30 = [c.close for c in candles_30] if candles_30 else None
    window_7 = [c.close for c in candles_7] if candles_7 else None
    volume_30 = [c.volume for c in candles_30 if c.volume is not None] if candles_30 else None
    # atr and donchian describe a 14 and 90 day shape, so they need their window covered
    # too. Without this a 2 candle item reports a confident 14 day ATR.
    atr_value = atr(window(candles, 14) or [])
    donchian = donchian_position(candles_90) if candles_90 else None

    return (
        PriceFeatures(
            median_7d=median(window_7) if window_7 else None,
            median_30d=median(window_30) if window_30 else None,
            median_90d=median(window_90) if window_90 else None,
            mad_90d=mad(window_90) if window_90 else None,
            robust_z=robust_z(last_close, window_90) if window_90 else None,
            percentile_90d=percentile_rank(last_close, window_90) if window_90 else None,
            atr_14d=atr_value,
            # `is not None` on the numerator: a genuinely flat item has atr 0.0, which is
            # a real reading of zero volatility, not an unmeasurable one.
            atr_pct=(atr_value / last_close) if atr_value is not None and last_close else None,
            volume_trend=volume_trend(candles),
            median_volume_30d=median(volume_30) if volume_30 else None,
            donchian_position=donchian,
            last_close=last_close,
        ),
        samples,
    )
