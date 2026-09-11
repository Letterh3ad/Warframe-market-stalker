from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from wfm.gui.deps import get_ctx
from wfm.services import news_jobs, news_service
from wfm.services.context import AppContext

router = APIRouter(prefix="/news", tags=["news"])


@router.get("/events")
async def list_events(
    limit: int = 50,
    offset: int = 0,
    event_type: str | None = None,
    source: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict:
    return news_service.events_page(
        ctx, limit=limit, offset=offset, event_type=event_type, source=source
    )


@router.get("/articles/{article_id}")
async def get_article(article_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    detail = news_service.article_detail(ctx, article_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"no article {article_id}")
    return detail


@router.get("/status")
async def get_status(ctx: AppContext = Depends(get_ctx)) -> dict:
    return {**news_service.status(ctx), "job": news_jobs.current()}


@router.post("/refresh")
async def start_refresh(ctx: AppContext = Depends(get_ctx)) -> dict:
    return news_jobs.spawn(ctx, "ingest")


@router.post("/classify")
async def start_classify(ctx: AppContext = Depends(get_ctx)) -> dict:
    return news_jobs.spawn(ctx, "classify")
