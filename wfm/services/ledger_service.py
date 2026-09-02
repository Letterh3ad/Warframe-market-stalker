from __future__ import annotations

from datetime import datetime

from wfm.ledger import pnl as pnl_module
from wfm.models import Side, Trade
from wfm.services import catalog_service
from wfm.services.context import AppContext


def _mark_for(ctx: AppContext, slug: str, rank: int) -> float | None:
    snapshot = ctx.orders.latest(slug, rank)
    if snapshot is None:
        return None
    return snapshot.online_best_bid if snapshot.online_best_bid is not None else snapshot.best_bid


def record(
    ctx: AppContext,
    side: str,
    query: str,
    quantity: int,
    platinum: int,
    rank: str | int | None = None,
    note: str | None = None,
    when: datetime | None = None,
) -> dict:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if platinum < 0:
        raise ValueError("platinum must not be negative")
    slug, ranks = catalog_service.resolve(ctx, query, rank)
    target_rank = ranks[0]
    side_enum = Side(side)

    if side_enum is Side.SELL:
        held = {(s, r): q for s, r, q, _ in ctx.trades.holdings()}.get((slug, target_rank), 0)
        if quantity > held:
            raise ValueError(
                f"cannot sell {quantity}, you hold {held} of {slug} rank {target_rank}"
            )

    trade = Trade(
        slug=slug, rank=target_rank, ts=when or ctx.clock.utcnow(), side=side_enum,
        quantity=quantity, platinum=platinum, note=note,
    )
    trade_id = ctx.trades.record(trade)
    return {
        "id": trade_id, "slug": slug, "rank": target_rank, "side": side,
        "quantity": quantity, "platinum": platinum,
    }


def holdings(ctx: AppContext) -> list[dict]:
    raw = ctx.trades.holdings()
    marks = {(slug, rank): _mark_for(ctx, slug, rank) for slug, rank, _, _ in raw}
    rows = pnl_module.unrealized(raw, {k: v for k, v in marks.items() if v is not None})
    for row in rows:
        item = ctx.items.get(row["slug"])
        row["name"] = item.name if item else row["slug"]
    return rows


def pnl(ctx: AppContext, since: datetime | None = None, realized_only: bool = False) -> dict:
    trades = [t for t in ctx.trades.all() if since is None or t.ts >= since]
    stats = pnl_module.summary(trades)
    payload = {
        "realized_profit": stats["realized_profit"],
        "trades": stats["trades"],
        "lots": stats["lots"],
    }
    if not realized_only:
        payload["open_positions"] = holdings(ctx)
    return payload
