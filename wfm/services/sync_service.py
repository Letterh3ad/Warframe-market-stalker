from __future__ import annotations

from wfm.services.context import AppContext
from wfm.sync.backfill import backfill_item
from wfm.sync.catalog import SWEEP_NAME as CATALOG_SWEEP
from wfm.sync.catalog import sync_catalog
from wfm.sync.sweep import SWEEP_NAME as BACKFILL_SWEEP
from wfm.sync.sweep import run_sweep


async def sync(ctx: AppContext, force: bool = False, dry_run: bool = False) -> dict:
    if dry_run:
        return {
            "dry_run": True,
            "changed": None,
            "item_count": ctx.items.count(),
            "requests_spent": 0,
            "would_spend": "1 request, plus 1 more if the catalog version moved",
        }
    result = await sync_catalog(
        ctx.new_client(), ctx.items, ctx.sweep_state, ctx.clock, force=force
    )
    return {
        "dry_run": False,
        "changed": result.changed,
        "version": result.version,
        "item_count": result.item_count,
        "requests_spent": result.requests_spent,
    }


async def backfill(
    ctx: AppContext, slug: str | None = None, limit: int | None = None, dry_run: bool = False
) -> dict:
    if dry_run:
        pending = 1 if slug else ctx.items.count()
        return {
            "dry_run": True,
            "processed": 0,
            "would_spend": pending,
            "estimated_minutes": round(pending / ctx.config.requests_per_second / 60, 1),
        }
    if slug is not None:
        result = await backfill_item(ctx.new_client(), slug, ctx.daily, ctx.hourly, ctx.clock)
        return {
            "dry_run": False,
            "processed": 1,
            "halted": False,
            "daily_written": result.daily_written,
            "hourly_written": result.hourly_written,
        }
    swept = await run_sweep(
        ctx.new_client(), ctx.items, ctx.daily, ctx.hourly, ctx.sweep_state, ctx.clock, limit=limit
    )
    return {
        "dry_run": False,
        "processed": swept.processed,
        "halted": swept.halted,
        "reason": swept.reason,
        "resumed_from": swept.resumed_from,
    }


def status(ctx: AppContext) -> dict:
    return {
        "items": ctx.items.count(),
        "catalog": ctx.sweep_state.get(CATALOG_SWEEP) or {"status": "never run"},
        "backfill": ctx.sweep_state.get(BACKFILL_SWEEP) or {"status": "never run"},
        "watched": len(ctx.watchlist.all()),
    }
