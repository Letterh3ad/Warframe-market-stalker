from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends

from wfm.gui.deps import get_ctx
from wfm.services import alert_service
from wfm.services.context import AppContext

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("")
async def list_signals_route(
    limit: int = 50,
    since: str | None = None,
    analyzer: str | None = None,
    slug: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> list[dict]:
    since_dt = datetime.fromisoformat(since) if since else None
    return alert_service.list_signals(
        ctx, since=since_dt, analyzer=analyzer, slug=slug, limit=limit
    )
