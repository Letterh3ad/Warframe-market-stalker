from __future__ import annotations

from wfm.models import Item
from wfm.services.context import AppContext


MAX_BROWSE_LIMIT = 500
"""A hand-edited URL must not be able to ask for the whole catalog in one response."""


def _as_dict(item: Item) -> dict:
    return {
        "slug": item.slug,
        "name": item.name,
        "tags": list(item.tags),
        "max_rank": item.max_rank,
        "canonical_rank": item.canonical_rank,
        "is_set": item.is_set,
    }


def search(ctx: AppContext, query: str, limit: int = 20) -> list[dict]:
    items = ctx.items.search(query, limit=limit)
    return [_as_dict(i) for i in sorted(items, key=lambda i: (i.tags[:1], i.name))]


def browse(
    ctx: AppContext, q: str | None = None, limit: int = 100, offset: int = 0
) -> dict:
    limit = max(1, min(int(limit), MAX_BROWSE_LIMIT))
    offset = max(0, int(offset))
    return {
        "total": ctx.items.count(q),
        "limit": limit,
        "offset": offset,
        "items": [_as_dict(i) for i in ctx.items.page(q, limit=limit, offset=offset)],
    }


def item_detail(ctx: AppContext, slug: str) -> dict | None:
    item = ctx.items.get(slug)
    return _as_dict(item) if item else None


def resolve(ctx: AppContext, query: str, rank: str | int | None = None) -> tuple[str, list[int]]:
    item = ctx.items.get(query)
    if item is None:
        matches = ctx.items.search(query, limit=2)
        if not matches:
            raise LookupError(f"no catalog item matches {query!r}. Try wfm search.")
        item = matches[0]
    if rank == "all":
        return item.slug, list(range(0, item.max_rank + 1))
    if rank is None:
        return item.slug, [item.canonical_rank]
    return item.slug, [int(rank)]
