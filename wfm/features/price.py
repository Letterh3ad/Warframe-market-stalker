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


def _sorted_dated(candles: list[DailyCandle]) -> list[tuple[date, DailyCandle]]:
    """Parse each candle's date once and sort by it, for reuse across the many windows
    one `build()` call slices. `sorted(..., key=...)` only ever compares the date half of
    the pair, so candles sharing a date keep their original relative order exactly as
    `sorted(candles, key=_day)` did.
    """
    return sorted(((_day(c), c) for c in candles), key=lambda pair: pair[0])


def _in_window(
    dated: list[tuple[date, DailyCandle]], days: int, end: date | None = None
) -> list[DailyCandle]:
    if not dated:
        return []
    last = end if end is not None else dated[-1][0]
    start = last - timedelta(days=days - 1)
    return [c for d, c in dated if start <= d <= last]


def in_window(candles: list[DailyCandle], days: int, end: date | None = None) -> list[DailyCandle]:
    """The candles falling in the `days` calendar days ending at `end`.

    Sorted here rather than trusting the caller, so one out-of-order candle cannot move
    the anchor. `end` falls back to the newest candle only for standalone use; a caller
    that knows the market's anchor date should always pass it.
    """
    return _in_window(_sorted_dated(candles), days, end)


def _covered(values: list, days: int) -> bool:
    return len(values) >= required_days(days)


def _window(
    dated: list[tuple[date, DailyCandle]], days: int, end: date | None = None
) -> list[DailyCandle] | None:
    inside = [c for c in _in_window(dated, days, end) if c.close is not None]
    return inside if _covered(inside, days) else None


def window(
    candles: list[DailyCandle], days: int, end: date | None = None
) -> list[DailyCandle] | None:
    """The window's candles, or None when too few of its days carry a close."""
    return _window(_sorted_dated(candles), days, end)


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


def _volumes_in(
    dated: list[tuple[date, DailyCandle]], days: int, end: date | None = None
) -> list | None:
    inside = [c.volume for c in _in_window(dated, days, end) if c.volume is not None]
    return inside if _covered(inside, days) else None


def volumes_in(candles: list[DailyCandle], days: int, end: date | None = None) -> list | None:
    """The window's volumes, or None when too few of its days carry one."""
    return _volumes_in(_sorted_dated(candles), days, end)


def _volume_trend(
    dated: list[tuple[date, DailyCandle]],
    short: int = 7,
    long: int = 30,
    end: date | None = None,
) -> float | None:
    long_volumes = _volumes_in(dated, long, end)
    short_volumes = _volumes_in(dated, short, end)
    if long_volumes is None or short_volumes is None:
        return None
    long_median = median(long_volumes)
    if not long_median:
        return None
    return median(short_volumes) / long_median


def volume_trend(
    candles: list[DailyCandle], short: int = 7, long: int = 30, end: date | None = None
) -> float | None:
    return _volume_trend(_sorted_dated(candles), short, long, end)


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


def _ranges_in(
    dated: list[tuple[date, DailyCandle]], days: int, end: date | None = None
) -> list | None:
    inside = [
        c
        for c in _in_window(dated, days, end)
        if c.high is not None and c.low is not None and c.close is not None
    ]
    return inside if _covered(inside, days) else None


def ranges_in(candles: list[DailyCandle], days: int, end: date | None = None) -> list | None:
    """The window's candles that carry a high, a low and a close.

    ATR reads all three, so gating it on close coverage alone lets an item whose highs
    are absent report a confident range built from one or two transitions.
    """
    return _ranges_in(_sorted_dated(candles), days, end)


def build(
    candles: list[DailyCandle], end: date | None = None
) -> tuple[PriceFeatures, dict[str, int]]:
    """A window statistic stays None unless the window clears MIN_COVERAGE. A "30 day
    median" taken from four points is exactly the plausible-looking wrong number this
    layer exists to avoid.
    """
    # Parsed and sorted once here rather than inside each of the ~13 window lookups
    # below, all of which slice the same series against the same anchor.
    dated = _sorted_dated(candles)

    # Each counter measures its own window, so a null statistic is always explained by
    # the sample count printed beside it.
    def _close_count(days: int) -> int:
        return len([c for c in _in_window(dated, days, end) if c.close is not None])

    def _volume_count(days: int) -> int:
        return len([c for c in _in_window(dated, days, end) if c.volume is not None])

    def _range_count(days: int) -> int:
        return len(
            [
                c
                for c in _in_window(dated, days, end)
                if c.high is not None and c.low is not None and c.close is not None
            ]
        )

    samples = {
        "price_90d": _close_count(90),
        "price_30d": _close_count(30),
        "price_7d": _close_count(7),
        "volume_30d": _volume_count(30),
        "volume_7d": _volume_count(7),
        "range_14d": _range_count(14),
    }
    # Taken from inside the longest window, not from the item's whole history: a dead
    # item's months-old price would otherwise print as its current one while every
    # window statistic around it was correctly null.
    in_longest = [c.close for c in _in_window(dated, 90, end) if c.close is not None]
    if not in_longest:
        return PriceFeatures(), samples

    last_close = in_longest[-1]
    candles_90 = _window(dated, 90, end)
    candles_30 = _window(dated, 30, end)
    candles_7 = _window(dated, 7, end)
    window_90 = [c.close for c in candles_90] if candles_90 else None
    window_30 = [c.close for c in candles_30] if candles_30 else None
    window_7 = [c.close for c in candles_7] if candles_7 else None
    volume_30 = _volumes_in(dated, 30, end)
    atr_candles = _ranges_in(dated, 14, end)
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
            volume_trend=_volume_trend(dated, end=end),
            median_volume_30d=median(volume_30) if volume_30 else None,
            donchian_position=donchian,
            last_close=last_close,
        ),
        samples,
    )
