from __future__ import annotations

import statistics
from datetime import datetime, timedelta

from wfm.features.types import SeasonalityFeatures
from wfm.models import HourlyCandle

BUCKETS = 168

MAX_OBSERVATION_AGE_HOURS = 3.0
"""How old the newest candle may be and still describe the present.

Measured against the real catalog: after one sweep the newest hourly candle ranged
from the current hour to many hours back, because a candle exists only for an hour in
which the item traded. Three hours keeps an ordinary quiet spell usable while refusing
a feed that has actually stopped.
"""


def bucket_of(ts: datetime) -> int:
    return ts.weekday() * 24 + ts.hour


def profile(candles: list[HourlyCandle]) -> dict[int, dict]:
    grouped: dict[int, list[HourlyCandle]] = {}
    for candle in candles:
        grouped.setdefault(bucket_of(candle.ts), []).append(candle)
    out: dict[int, dict] = {}
    for bucket, entries in grouped.items():
        volumes = [c.volume for c in entries if c.volume is not None]
        prices = [c.close for c in entries if c.close is not None]
        # n counts the rows that actually carried a price, not every row in the bucket.
        # Counting all of them reports a confident expectation built from one observation.
        out[bucket] = {
            "n": len(prices),
            "n_volume": len(volumes),
            "volume": statistics.median(volumes) if volumes else None,
            "price": statistics.median(prices) if prices else None,
        }
    return out


def build(
    candles: list[HourlyCandle],
    now: datetime,
    min_samples: int = 4,
    max_observation_age_hours: float = MAX_OBSERVATION_AGE_HOURS,
) -> tuple[SeasonalityFeatures, dict[str, int]]:
    """The confidence figure is the point of this module. Two weeks of data can describe
    a weekly rhythm that does not exist, so analyzers gate on it rather than reading the
    deviation directly.
    """
    # An hourly candle exists only for an hour in which the item traded, so the newest
    # one is "when it last traded", which is normally an hour or two back and sometimes
    # much further. Two rules follow, and both have been got wrong before:
    #
    #   Recency, not bucket equality. Requiring the observation to sit in now's bucket
    #   kills the deviations outright, since the item usually did not trade this hour.
    #   Accepting any bucket match is worse: hour-of-week repeats every 168 hours, so a
    #   feed that died a week ago would report a confident reading about the present.
    #
    #   The expectation belongs to the observation's own bucket, because that is the
    #   hour the deviation is describing. observed_age_hours is published so a consumer
    #   can see how current that hour is.
    observed = sorted([c for c in candles if c.ts <= now], key=lambda c: c.ts)
    newest = observed[-1] if observed else None
    age_hours = (now - newest.ts).total_seconds() / 3600 if newest else None
    latest = newest if age_hours is not None and age_hours <= max_observation_age_hours else None
    history = [c for c in observed if c is not latest]

    grouped = profile(history)
    reference_bucket = bucket_of(latest.ts) if latest else bucket_of(now)
    stats = grouped.get(reference_bucket, {"n": 0, "n_volume": 0, "volume": None, "price": None})
    samples = {"seasonality_bucket": stats["n"], "seasonality_total": len(history)}

    expected_volume = stats["volume"]
    expected_price = stats["price"]

    volume_deviation = (
        (latest.volume - expected_volume) / expected_volume
        if latest and latest.volume is not None and expected_volume
        else None
    )
    price_deviation = (
        (latest.close - expected_price) / expected_price
        if latest and latest.close is not None and expected_price
        else None
    )

    upcoming = {bucket_of(now + timedelta(hours=h)) for h in range(1, 49)}
    priced = [
        (bucket, grouped[bucket]["price"])
        for bucket in upcoming
        if bucket in grouped
        and grouped[bucket]["price"] is not None
        and grouped[bucket]["n"] >= min_samples
    ]
    best_bucket = max(priced, key=lambda pair: pair[1])[0] if priced else None

    return (
        SeasonalityFeatures(
            bucket=bucket_of(now),
            observed_bucket=reference_bucket if latest else None,
            observed_age_hours=age_hours if latest else None,
            expected_volume=expected_volume,
            expected_price=expected_price,
            volume_deviation=volume_deviation,
            price_deviation=price_deviation,
            confidence=min(1.0, stats["n"] / min_samples) if min_samples else 0.0,
            best_bucket_next_48h=best_bucket,
        ),
        samples,
    )
