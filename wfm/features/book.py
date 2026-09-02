from __future__ import annotations

from datetime import datetime, timedelta

from wfm.features.types import BookFeatures
from wfm.models import BookSnapshot, Order, Side


def depth_curve(orders: list[Order], levels: int = 5) -> tuple[int, ...]:
    if not orders:
        return ()
    by_price: dict[int, int] = {}
    for order in orders:
        by_price[order.platinum] = by_price.get(order.platinum, 0) + order.quantity
    ascending = orders[0].side is Side.SELL
    prices = sorted(by_price, reverse=not ascending)[:levels]
    curve: list[int] = []
    running = 0
    for price in prices:
        running += by_price[price]
        curve.append(running)
    return tuple(curve)


def summarize(
    orders: list[Order],
    slug: str,
    rank: int,
    ts: datetime,
    stale_after_days: int = 7,
) -> BookSnapshot:
    """Online best prices are carried alongside the raw ones rather than replacing them:
    only online sellers are tradeable, but the raw pair is what shows how much of the
    visible book is unreachable.
    """
    visible = [o for o in orders if o.visible and o.rank == rank]
    asks = sorted([o for o in visible if o.side is Side.SELL], key=lambda o: o.platinum)
    bids = sorted(
        [o for o in visible if o.side is Side.BUY], key=lambda o: o.platinum, reverse=True
    )
    online_asks = [o for o in asks if o.is_online]
    online_bids = [o for o in bids if o.is_online]

    cutoff = ts - timedelta(days=stale_after_days)
    stale = [
        o for o in visible if not o.is_online and (o.updated_at is None or o.updated_at < cutoff)
    ]

    return BookSnapshot(
        slug=slug,
        rank=rank,
        ts=ts,
        best_ask=asks[0].platinum if asks else None,
        best_bid=bids[0].platinum if bids else None,
        online_best_ask=online_asks[0].platinum if online_asks else None,
        online_best_bid=online_bids[0].platinum if online_bids else None,
        bid_depth=depth_curve(bids),
        ask_depth=depth_curve(asks),
        online_bid_depth=depth_curve(online_bids),
        online_ask_depth=depth_curve(online_asks),
        bid_count=len(bids),
        ask_count=len(asks),
        online_bid_count=len(online_bids),
        online_ask_count=len(online_asks),
        stale_share=(len(stale) / len(visible)) if visible else None,
    )


def build(snapshot: BookSnapshot) -> tuple[BookFeatures, dict[str, int]]:
    samples = {"book": snapshot.bid_count + snapshot.ask_count}
    total_bid = snapshot.bid_depth[-1] if snapshot.bid_depth else 0
    total_ask = snapshot.ask_depth[-1] if snapshot.ask_depth else 0
    total = total_bid + total_ask

    return (
        BookFeatures(
            best_bid=snapshot.best_bid,
            best_ask=snapshot.best_ask,
            online_best_bid=snapshot.online_best_bid,
            online_best_ask=snapshot.online_best_ask,
            spread=snapshot.spread,
            online_spread=snapshot.online_spread,
            spread_pct=(snapshot.spread / snapshot.best_ask)
            if snapshot.spread is not None and snapshot.best_ask
            else None,
            online_spread_pct=(snapshot.online_spread / snapshot.online_best_ask)
            if snapshot.online_spread is not None and snapshot.online_best_ask
            else None,
            bid_depth=snapshot.bid_depth,
            ask_depth=snapshot.ask_depth,
            online_bid_depth=snapshot.online_bid_depth,
            online_ask_depth=snapshot.online_ask_depth,
            imbalance=(total_bid / total) if total else None,
            stale_share=snapshot.stale_share,
            bid_count=snapshot.bid_count,
            ask_count=snapshot.ask_count,
            online_bid_count=snapshot.online_bid_count,
            online_ask_count=snapshot.online_ask_count,
        ),
        samples,
    )
