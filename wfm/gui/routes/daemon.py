from __future__ import annotations

from fastapi import APIRouter, Depends

from wfm.gui.deps import get_ctx
from wfm.services import daemon_service
from wfm.services.context import AppContext

router = APIRouter(prefix="/daemon", tags=["daemon"])


@router.get("/status")
def get_status(ctx: AppContext = Depends(get_ctx)) -> dict:
    return daemon_service.status(ctx)


@router.post("/stop")
def stop_daemon(ctx: AppContext = Depends(get_ctx)) -> dict:
    return daemon_service.stop(ctx)
