from __future__ import annotations

import statistics
from datetime import datetime, timedelta

from wfm.features.types import SeasonalityFeatures
from wfm.models import HourlyCandle

BUCKETS = 168


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
        out[bucket] = {
            "n": len(entries),
            "volume": statistics.median(volumes) if volumes else None,
            "price": statistics.median(prices) if prices else None,
        }
    return out


def build(
    candles: list[HourlyCandle], now: datetime, min_samples: int = 4
) -> tuple[SeasonalityFeatures, dict[str, int]]:
    """The confidence figure is the point of this module. Two weeks of data can describe
    a weekly rhythm that does not exist, so analyzers gate on it rather than reading the
    deviation directly.
    """
    current_bucket = bucket_of(now)
    history = [c for c in candles if c.ts < now]
    grouped = profile(history)
    stats = grouped.get(current_bucket, {"n": 0, "volume": None, "price": None})
    samples = {"seasonality_bucket": stats["n"], "seasonality_total": len(history)}

    latest = max((c for c in candles if c.ts >= now), key=lambda c: c.ts, default=None)
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
            bucket=current_bucket,
            expected_volume=expected_volume,
            expected_price=expected_price,
            volume_deviation=volume_deviation,
            price_deviation=price_deviation,
            confidence=min(1.0, stats["n"] / min_samples) if min_samples else 0.0,
            best_bucket_next_48h=best_bucket,
        ),
        samples,
    )
