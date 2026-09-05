from __future__ import annotations

from fastapi import APIRouter, Depends

from wfm.gui.deps import get_ctx
from wfm.gui.market_cache import get_market
from wfm.services import report_service
from wfm.services.context import AppContext

router = APIRouter(prefix="/items", tags=["items"])


@router.get("/{slug}")
async def get_item(
    slug: str, rank: str | int | None = None, refresh: bool = False,
    ctx: AppContext = Depends(get_ctx),
    market=Depends(get_market),
) -> dict:
    return await report_service.report(
        ctx, slug, rank=rank, refresh=refresh, market=market
    )


@router.get("/{slug}/history")
async def get_item_history(
    slug: str,
    rank: str | int | None = None,
    days: int = 90,
    ctx: AppContext = Depends(get_ctx),
) -> list[dict]:
    return report_service.history(ctx, slug, rank=rank, days=days)
