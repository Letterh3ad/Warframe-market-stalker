from __future__ import annotations

from collections import deque

from wfm.models import Side, Trade


def realized(trades: list[Trade]) -> list[dict]:
    """FIFO lot matching, per (slug, rank).

    Average cost would be simpler but would hide the thing the ledger exists to show:
    which specific buys a sale actually closed out.
    """
    lots: list[dict] = []
    open_buys: dict[tuple[str, int], deque] = {}
    for trade in sorted(trades, key=lambda t: (t.ts, t.id or 0)):
        key = (trade.slug, trade.rank)
        queue = open_buys.setdefault(key, deque())
        if trade.side is Side.BUY:
            queue.append([trade.quantity, trade.platinum])
            continue
        remaining = trade.quantity
        while remaining > 0 and queue:
            lot_quantity, lot_price = queue[0]
            matched = min(remaining, lot_quantity)
            lots.append(
                {
                    "slug": trade.slug,
                    "rank": trade.rank,
                    "quantity": matched,
                    "cost": matched * lot_price,
                    "proceeds": matched * trade.platinum,
                    "profit": matched * (trade.platinum - lot_price),
                    "sold_at": trade.ts.isoformat(),
                }
            )
            remaining -= matched
            if matched == lot_quantity:
                queue.popleft()
            else:
                queue[0][0] -= matched
    return lots


def summary(trades: list[Trade]) -> dict:
    lots = realized(trades)
    by_item: dict[tuple[str, int], dict] = {}
    for lot in lots:
        key = (lot["slug"], lot["rank"])
        entry = by_item.setdefault(key, {"quantity": 0, "profit": 0})
        entry["quantity"] += lot["quantity"]
        entry["profit"] += lot["profit"]
    return {
        "realized_profit": sum(lot["profit"] for lot in lots),
        "trades": len(trades),
        "lots": lots,
        "by_item": by_item,
    }


def unrealized(holdings: list[tuple], marks: dict[tuple[str, int], float]) -> list[dict]:
    rows = []
    for slug, rank, quantity, avg_cost in holdings:
        mark = marks.get((slug, rank))
        rows.append(
            {
                "slug": slug,
                "rank": rank,
                "quantity": quantity,
                "avg_cost": avg_cost,
                "mark": mark,
                "unrealized_profit": (mark - avg_cost) * quantity if mark is not None else None,
            }
        )
    return rows
