from __future__ import annotations

from wfm.services import catalog_service
from wfm.services.context import AppContext


def new(ctx: AppContext, name: str) -> dict:
    group = ctx.groups.create(name, ctx.clock.utcnow())
    return {"name": group.name, "id": group.id}


def rm(ctx: AppContext, name: str) -> dict:
    return {"name": name, "removed": ctx.groups.delete(name)}


def ls(ctx: AppContext) -> list[dict]:
    return [
        {"name": g.name, "members": len(ctx.groups.members(g.name))} for g in ctx.groups.all()
    ]


def add(ctx: AppContext, name: str, query: str, rank: str | int | None = None) -> dict:
    slug, ranks = catalog_service.resolve(ctx, query, rank)
    for r in ranks:
        ctx.groups.add_member(name, slug, r)
    return {"group": name, "slug": slug, "ranks": ranks}


def remove(ctx: AppContext, name: str, query: str, rank: str | int | None = None) -> dict:
    slug, ranks = catalog_service.resolve(ctx, query, rank)
    removed = sum(1 for r in ranks if ctx.groups.remove_member(name, slug, r)) > 0
    return {"group": name, "slug": slug, "removed": removed}


def show(ctx: AppContext, name: str) -> dict:
    members = []
    for slug, rank in ctx.groups.members(name):
        item = ctx.items.get(slug)
        members.append({"slug": slug, "rank": rank, "name": item.name if item else slug})
    return {"name": name, "members": members}
