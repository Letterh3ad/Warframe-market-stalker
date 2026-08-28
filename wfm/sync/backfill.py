from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from wfm.api.endpoints import fetch_statistics
from wfm.clock import Clock
from wfm.store.stats import DailyStatsRepo, HourlyStatsRepo
from wfm.sync.budget import Priority

HOURLY_RETENTION_DAYS = 14


@dataclass(frozen=True)
class BackfillResult:
    slug: str
    daily_written: int
    hourly_written: int
    skipped: bool = False


async def backfill_item(
    client,
    slug: str,
    daily_repo: DailyStatsRepo,
    hourly_repo: HourlyStatsRepo,
    clock: Clock,
    priority: Priority = Priority.BULK,
) -> BackfillResult:
    daily, hourly = await fetch_statistics(client, slug, priority=priority)

    latest_by_rank = {
        rank: daily_repo.latest_date(slug, rank) for rank in {c.rank for c in daily}
    }
    fresh_daily = [
        candle
        for candle in daily
        if latest_by_rank.get(candle.rank) is None or candle.date > latest_by_rank[candle.rank]
    ]
    daily_repo.upsert_many(fresh_daily)
    hourly_repo.upsert_many(hourly)
    hourly_repo.prune(before=clock.utcnow() - timedelta(days=HOURLY_RETENTION_DAYS))
    return BackfillResult(slug, len(fresh_daily), len(hourly))
