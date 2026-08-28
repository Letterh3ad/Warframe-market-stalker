from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from wfm.api.errors import ApiError, CircuitOpen
from wfm.clock import Clock
from wfm.models import SweepStatus
from wfm.store.items import ItemsRepo
from wfm.store.stats import DailyStatsRepo, HourlyStatsRepo
from wfm.store.sweep import SweepStateRepo
from wfm.sync.backfill import backfill_item
from wfm.sync.budget import Priority

SWEEP_NAME = "backfill"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepResult:
    processed: int
    halted: bool = False
    reason: str | None = None
    resumed_from: str | None = None


async def run_sweep(
    client,
    items_repo: ItemsRepo,
    daily_repo: DailyStatsRepo,
    hourly_repo: HourlyStatsRepo,
    sweep_state_repo: SweepStateRepo,
    clock: Clock,
    limit: int | None = None,
    on_progress: Callable[[str, int], None] | None = None,
) -> SweepResult:
    previous = sweep_state_repo.get(SWEEP_NAME) or {}
    resume_after = (
        previous.get("cursor") if previous.get("status") == SweepStatus.RUNNING.value else None
    )
    done_count = int(previous.get("done_count") or 0) if resume_after else 0

    slugs = [s for s in items_repo.all_slugs() if resume_after is None or s > resume_after]
    sweep_state_repo.start(SWEEP_NAME, clock.utcnow())
    if resume_after:
        sweep_state_repo.checkpoint(SWEEP_NAME, resume_after, clock.utcnow(), done_count)

    processed = 0
    for slug in slugs:
        if limit is not None and processed >= limit:
            return SweepResult(processed, resumed_from=resume_after)
        try:
            await backfill_item(
                client, slug, daily_repo, hourly_repo, clock, priority=Priority.BULK
            )
        except CircuitOpen as exc:
            sweep_state_repo.halt(SWEEP_NAME, reason=exc.reason, when=clock.utcnow())
            return SweepResult(processed, halted=True, reason=exc.reason, resumed_from=resume_after)
        except ApiError as exc:
            log.warning("skipping %s: %s", slug, exc)

        processed += 1
        done_count += 1
        sweep_state_repo.checkpoint(SWEEP_NAME, slug, clock.utcnow(), done_count)
        if on_progress is not None:
            on_progress(slug, processed)

    sweep_state_repo.finish(SWEEP_NAME, clock.utcnow())
    return SweepResult(processed, resumed_from=resume_after)


def sweep_status(sweep_state_repo: SweepStateRepo) -> dict:
    return sweep_state_repo.get(SWEEP_NAME) or {"status": "never run"}
