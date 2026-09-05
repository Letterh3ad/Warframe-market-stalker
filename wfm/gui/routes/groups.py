from __future__ import annotations

from fastapi import APIRouter, Depends

from wfm.gui.deps import get_ctx
from wfm.gui.schemas import GroupCreateRequest, GroupMemberRequest
from wfm.services import analysis_service, group_service
from wfm.services.context import AppContext

router = APIRouter(prefix="/groups", tags=["groups"])


@router.get("")
async def list_groups(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    return group_service.ls(ctx)


@router.post("")
async def create_group(body: GroupCreateRequest, ctx: AppContext = Depends(get_ctx)) -> dict:
    return group_service.new(ctx, body.name)


@router.delete("/{name}")
async def delete_group(name: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    return group_service.rm(ctx, name)


@router.get("/{name}")
async def show_group(name: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    return group_service.show(ctx, name)


@router.post("/{name}/members")
async def add_member(
    name: str, body: GroupMemberRequest, ctx: AppContext = Depends(get_ctx)
) -> dict:
    return group_service.add(ctx, name, body.query, rank=body.rank)


@router.delete("/{name}/members")
async def remove_member(
    name: str, body: GroupMemberRequest, ctx: AppContext = Depends(get_ctx)
) -> dict:
    return group_service.remove(ctx, name, body.query, rank=body.rank)


@router.get("/{name}/analysis")
async def analyze_group_route(name: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    # persist=False: GET must be idempotent. With persist=True, dedup/cooldown on
    # group signals (2026-09-05 decision) meant a second request within the cooldown
    # window returned an empty group_signals list, hiding a still-open opportunity.
    return analysis_service.analyze_group(ctx, name, persist=False)
