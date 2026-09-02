"""Candle builders shaped like the data warframe.market actually returns.

Three rounds of phase 4 review found bugs that the tests could not see, because the
fixtures described data the API never produces. Measured against the real database:

- Hourly timestamps land exactly on hour boundaries, never with a minute component.
- An hourly candle exists only for an hour in which the item TRADED. Gaps are normal and
  mean "no trades", not "no data". Across 2700 items the newest candle ranged from the
  current hour to many hours back, so the newest candle is not reliably recent.
- Daily candles cover complete days only, so the newest is yesterday, and the API never
  returns more than 89 of them.

Build fixtures from these helpers rather than by hand, so a test cannot pass against a
world that cannot happen.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from wfm.models import DailyCandle, HourlyCandle


def hour_floor(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


def hourly_history(
    now: datetime,
    weeks: int = 6,
    newest_age_hours: int = 1,
    volume: int = 10,
    close: float = 40.0,
    slug: str = "x",
    rank: int = 0,
) -> list[HourlyCandle]:
    """A dense hourly history ending `newest_age_hours` before `now`.

    `newest_age_hours` is explicit because it is the thing that keeps breaking: the
    newest candle is whenever the item last traded, which may be this hour or hours ago.
    """
    newest = hour_floor(now) - timedelta(hours=newest_age_hours)
    return [
        HourlyCandle(
            slug=slug, rank=rank, ts=newest - timedelta(hours=h),
            volume=volume, close=close, median=close,
        )
        for h in range(weeks * 7 * 24)
    ][::-1]


def hourly_at(
    now: datetime,
    age_hours: int,
    volume: int,
    close: float,
    slug: str = "x",
    rank: int = 0,
) -> HourlyCandle:
    """One candle `age_hours` before `now`, on the hour."""
    return HourlyCandle(
        slug=slug, rank=rank, ts=hour_floor(now) - timedelta(hours=age_hours),
        volume=volume, close=close, median=close,
    )


def same_bucket_history(
    now: datetime,
    weeks: int,
    age_hours: int = 1,
    volume: int = 10,
    close: float = 40.0,
    slug: str = "x",
    rank: int = 0,
) -> list[HourlyCandle]:
    """`weeks` candles in the same hour-of-week bucket as `now - age_hours`.

    An hour-of-week bucket recurs weekly, which is why the profile needs weeks of history
    before it can say anything.
    """
    anchor = hour_floor(now) - timedelta(hours=age_hours)
    return [
        HourlyCandle(
            slug=slug, rank=rank, ts=anchor - timedelta(weeks=w),
            volume=volume, close=close, median=close,
        )
        for w in range(1, weeks + 1)
    ]


def daily_history(
    end: date,
    days: int,
    close: float = 40.0,
    volume: int = 10,
    high: float | None = None,
    low: float | None = None,
    slug: str = "x",
    rank: int = 0,
) -> list[DailyCandle]:
    """`days` consecutive complete daily candles ending at `end`, oldest first."""
    return [
        DailyCandle(
            slug=slug, rank=rank,
            date=(end - timedelta(days=days - 1 - i)).isoformat(),
            close=close, median=close, volume=volume,
            high=close + 2 if high is None else high,
            low=close - 2 if low is None else low,
        )
        for i in range(days)
    ]
