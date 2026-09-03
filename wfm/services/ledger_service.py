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


def _resolve_for_trade(ctx: AppContext, query: str, rank: str | int | None):
    # A trade records money. Unlike a read-only lookup it must not silently pick the
    # first fuzzy match: require an exact name/slug, or a single candidate. A miss or a
    # genuinely ambiguous name falls through to catalog_service.resolve, which raises the
    # standard "no catalog item matches" / picks the sole hit.
    if ctx.items.get(query) is None:
        matches = ctx.items.search(query, limit=25)
        exact = [
            m for m in matches
            if m.name.lower() == query.lower() or m.slug == query.lower()
        ]
        if exact:
            query = exact[0].slug
        elif len(matches) > 1:
            listed = ", ".join(f"{m.name!r} ({m.slug})" for m in matches)
            raise ValueError(
                f"{query!r} is ambiguous for a trade; it matches {listed}. "
                "Pass the exact name or slug."
            )
    return catalog_service.resolve(ctx, query, rank)


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
    slug, ranks = _resolve_for_trade(ctx, query, rank)
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


def cost_basis(ctx: AppContext) -> dict[tuple[str, int], float]:
    """FIFO remainder cost basis, keyed like the holdings view. The view's own avg_cost
    blends every buy including closed-out lots; this is the corrected figure.
    """
    return pnl_module.cost_basis(ctx.trades.all())


def holdings(ctx: AppContext) -> list[dict]:
    basis = cost_basis(ctx)
    raw = [
        (slug, rank, quantity, basis.get((slug, rank), avg_cost))
        for slug, rank, quantity, avg_cost in ctx.trades.holdings()
    ]
    marks = {(slug, rank): _mark_for(ctx, slug, rank) for slug, rank, _, _ in raw}
    rows = pnl_module.unrealized(raw, {k: v for k, v in marks.items() if v is not None})
    for row in rows:
        item = ctx.items.get(row["slug"])
        row["name"] = item.name if item else row["slug"]
    return rows


def pnl(ctx: AppContext, since: datetime | None = None, realized_only: bool = False) -> dict:
    # FIFO must see every trade: a sale inside the window is matched against buys that
    # may predate it. `since` then filters the reported lots by when they were sold,
    # never the matcher's input.
    all_trades = ctx.trades.all()
    lots = pnl_module.realized(all_trades)
    if since is not None:
        lots = [lot for lot in lots if datetime.fromisoformat(lot["sold_at"]) >= since]
    payload = {
        "realized_profit": sum(lot["profit"] for lot in lots),
        "trades": sum(1 for t in all_trades if since is None or t.ts >= since),
        "lots": lots,
    }
    if not realized_only:
        payload["open_positions"] = holdings(ctx)
    return payload
