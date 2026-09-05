from __future__ import annotations

from fastapi import APIRouter, Depends

from wfm.gui.deps import get_ctx
from wfm.gui.schemas import WatchlistAddRequest
from wfm.services import watch_service
from wfm.services.context import AppContext

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("")
def list_watchlist(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    return watch_service.list_(ctx)


@router.post("")
def add_to_watchlist(
    body: WatchlistAddRequest, ctx: AppContext = Depends(get_ctx)
) -> dict:
    return watch_service.add(ctx, body.query, rank=body.rank, pin=body.pin, alert=body.alert)


@router.delete("/{slug}/{rank}")
def remove_from_watchlist(slug: str, rank: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    return watch_service.remove(ctx, slug, rank=rank)
