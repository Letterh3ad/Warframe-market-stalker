from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends

from wfm.gui.deps import get_ctx
from wfm.services import ledger_service
from wfm.services.context import AppContext

router = APIRouter(prefix="/ledger", tags=["ledger"])


@router.get("/holdings")
async def get_holdings(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    return ledger_service.holdings(ctx)


@router.get("/pnl")
async def get_pnl(
    since: str | None = None, realized_only: bool = False,
    ctx: AppContext = Depends(get_ctx),
) -> dict:
    since_dt = datetime.fromisoformat(since) if since else None
    return ledger_service.pnl(ctx, since=since_dt, realized_only=realized_only)
