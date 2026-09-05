from __future__ import annotations

from fastapi import APIRouter, Depends

from wfm.gui.deps import get_ctx
from wfm.services import catalog_service
from wfm.services.context import AppContext

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("")
async def browse_catalog(
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    ctx: AppContext = Depends(get_ctx),
) -> dict:
    return catalog_service.browse(ctx, q=q, limit=limit, offset=offset)
