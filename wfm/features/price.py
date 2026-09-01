from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

from wfm.features.types import PriceFeatures
from wfm.models import DailyCandle

MAD_TO_SD = 0.6745
"""Scale factor making MAD comparable to a standard deviation for normal data."""

MIN_COVERAGE = 0.9
"""Share of a window's days that must carry data before its statistics are computed."""

# The windowing contract, in one place so that changing it is a deliberate act:
#
# 1. A window is `days` CALENDAR DAYS ending at `end`, an anchor the caller supplies.
#    Never derived from the item's own newest candle, or a dead item's last dense run
#    reads as current data.
# 2. A statistic exists only if the days inside the window carrying a usable value reach
#    required_days(days). At least one missing day is tolerated at every size, because
#    ceil(days * MIN_COVERAGE) == days for every window of nine days or fewer, which
#    would make the tolerance silently vanish exactly where gaps are most common.
# 3. Coverage is counted over THE VALUES THE STATISTIC USES: closes for the price
#    figures, volumes for the volume figures, high/low/close for the range figures.
#    Gating one on another is how a confident median gets built from two samples.


def required_days(days: int) -> int:
    # Read from MIN_COVERAGE so the constant is the knob it is documented to be, but
    # never stricter than "one day may be missing": ceil(days * 0.9) == days for every
    # window of nine days or fewer, which would silently remove the tolerance exactly
    # where gaps are most common.
    return min(math.ceil(days * MIN_COVERAGE), days - 1) if days > 1 else days


def _day(candle: DailyCandle) -> date:
    return date.fromisoformat(candle.date)


def in_window(candles: list[DailyCandle], days: int, end: date | None = None) -> list[DailyCandle]:
    """The candles falling in the `days` calendar days ending at `end`.

    Sorted here rather than trusting the caller, so one out-of-order candle cannot move
    the anchor. `end` falls back to the newest candle only for standalone use; a caller
    that knows the market's anchor date should always pass it.
    """
    dated = sorted(candles, key=_day)
    if not dated:
        return []
    last = end if end is not None else _day(dated[-1])
    start = last - timedelta(days=days - 1)
    return [c for c in dated if start <= _day(c) <= last]


def _covered(values: list, days: int) -> bool:
    return len(values) >= required_days(days)


def window(
    candles: list[DailyCandle], days: int, end: date | None = None
) -> list[DailyCandle] | None:
    """The window's candles, or None when too few of its days carry a close."""
    inside = [c for c in in_window(candles, days, end) if c.close is not None]
    return inside if _covered(inside, days) else None


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


def volumes_in(candles: list[DailyCandle], days: int, end: date | None = None) -> list | None:
    """The window's volumes, or None when too few of its days carry one."""
    inside = [c.volume for c in in_window(candles, days, end) if c.volume is not None]
    return inside if _covered(inside, days) else None


def volume_trend(
    candles: list[DailyCandle], short: int = 7, long: int = 30, end: date | None = None
) -> float | None:
    long_volumes = volumes_in(candles, long, end)
    short_volumes = volumes_in(candles, short, end)
    if long_volumes is None or short_volumes is None:
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


def ranges_in(candles: list[DailyCandle], days: int, end: date | None = None) -> list | None:
    """The window's candles that carry a high, a low and a close.

    ATR reads all three, so gating it on close coverage alone lets an item whose highs
    are absent report a confident range built from one or two transitions.
    """
    inside = [
        c
        for c in in_window(candles, days, end)
        if c.high is not None and c.low is not None and c.close is not None
    ]
    return inside if _covered(inside, days) else None


def build(
    candles: list[DailyCandle], end: date | None = None
) -> tuple[PriceFeatures, dict[str, int]]:
    """A window statistic stays None unless the window clears MIN_COVERAGE. A "30 day
    median" taken from four points is exactly the plausible-looking wrong number this
    layer exists to avoid.
    """
    # Each counter measures its own window, so a null statistic is always explained by
    # the sample count printed beside it.
    def _closes_in(days: int) -> int:
        return len([c for c in in_window(candles, days, end) if c.close is not None])

    samples = {
        "price_90d": _closes_in(90),
        "price_30d": _closes_in(30),
        "price_7d": _closes_in(7),
        "volume_30d": len(
            [c for c in in_window(candles, 30, end) if c.volume is not None]
        ),
    }
    # Taken from inside the longest window, not from the item's whole history: a dead
    # item's months-old price would otherwise print as its current one while every
    # window statistic around it was correctly null.
    in_longest = [c.close for c in in_window(candles, 90, end) if c.close is not None]
    if not in_longest:
        return PriceFeatures(), samples

    last_close = in_longest[-1]
    candles_90 = window(candles, 90, end)
    candles_30 = window(candles, 30, end)
    candles_7 = window(candles, 7, end)
    window_90 = [c.close for c in candles_90] if candles_90 else None
    window_30 = [c.close for c in candles_30] if candles_30 else None
    window_7 = [c.close for c in candles_7] if candles_7 else None
    volume_30 = volumes_in(candles, 30, end)
    atr_candles = ranges_in(candles, 14, end)
    atr_value = atr(atr_candles) if atr_candles else None
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
            volume_trend=volume_trend(candles, end=end),
            median_volume_30d=median(volume_30) if volume_30 else None,
            donchian_position=donchian,
            last_close=last_close,
        ),
        samples,
    )
