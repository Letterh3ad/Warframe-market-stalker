from datetime import datetime, timedelta, timezone

import pytest

from wfm.features.book import build, depth_curve, summarize
from wfm.models import Order, Side

TS = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
FRESH = TS - timedelta(days=1)
OLD = TS - timedelta(days=60)


def _o(plat, qty, side, status="ingame", visible=True, updated=FRESH, rank=0):
    return Order(
        platinum=plat, quantity=qty, rank=rank, side=side,
        visible=visible, user_status=status, updated_at=updated,
    )


BOOK = [
    _o(45, 1, Side.SELL, status="ingame"),
    _o(44, 2, Side.SELL, status="offline", updated=OLD),
    _o(48, 3, Side.SELL, status="online"),
    _o(50, 5, Side.SELL, status="offline", updated=OLD),
    _o(40, 2, Side.BUY, status="online"),
    _o(38, 1, Side.BUY, status="offline", updated=OLD),
    _o(30, 9, Side.BUY, status="ingame"),
]


def test_summarize_reports_raw_and_online_best_prices():
    snap = summarize(BOOK, slug="x", rank=0, ts=TS)
    assert snap.best_ask == 44
    assert snap.online_best_ask == 45
    assert snap.best_bid == 40
    assert snap.online_best_bid == 40
    assert snap.spread == 4
    assert snap.online_spread == 5


def test_summarize_counts_orders_by_side_and_status():
    snap = summarize(BOOK, slug="x", rank=0, ts=TS)
    assert snap.ask_count == 4
    assert snap.bid_count == 3
    assert snap.online_ask_count == 2
    assert snap.online_bid_count == 2


def test_summarize_ignores_invisible_orders():
    book = BOOK + [_o(1, 99, Side.SELL, visible=False)]
    assert summarize(book, slug="x", rank=0, ts=TS).best_ask == 44


def test_summarize_filters_by_rank():
    book = BOOK + [_o(5, 1, Side.SELL, rank=10)]
    assert summarize(book, slug="x", rank=0, ts=TS).best_ask == 44
    assert summarize(book, slug="x", rank=10, ts=TS).best_ask == 5


def test_stale_share_counts_offline_orders_older_than_the_threshold():
    snap = summarize(BOOK, slug="x", rank=0, ts=TS, stale_after_days=7)
    assert snap.stale_share == pytest.approx(3 / 7)


def test_depth_curve_is_cumulative_over_the_best_five_prices():
    asks = [o for o in BOOK if o.side is Side.SELL]
    assert depth_curve(asks, levels=5) == (2, 3, 6, 11)


def test_depth_curve_of_an_empty_side_is_empty():
    assert depth_curve([], levels=5) == ()


def test_an_empty_book_summarizes_without_raising():
    snap = summarize([], slug="x", rank=0, ts=TS)
    assert snap.best_ask is None
    assert snap.best_bid is None
    assert snap.spread is None
    assert snap.stale_share is None
    assert snap.ask_count == 0


def test_a_one_sided_book_has_no_spread():
    only_sells = [o for o in BOOK if o.side is Side.SELL]
    snap = summarize(only_sells, slug="x", rank=0, ts=TS)
    assert snap.best_ask == 44
    assert snap.best_bid is None
    assert snap.spread is None


def test_build_derives_percentages_and_imbalance():
    snap = summarize(BOOK, slug="x", rank=0, ts=TS)
    features, samples = build(snap)
    assert features.spread_pct == pytest.approx(4 / 44)
    assert features.online_spread_pct == pytest.approx(5 / 45)
    assert features.imbalance == pytest.approx(12 / 23)
    assert samples["book"] == 7


def test_build_of_an_empty_book_leaves_everything_none():
    features, samples = build(summarize([], slug="x", rank=0, ts=TS))
    assert features.spread_pct is None
    assert features.imbalance is None
    assert samples["book"] == 0


def test_summarize_reports_online_only_depth_curves():
    snap = summarize(BOOK, slug="x", rank=0, ts=TS)
    # online asks in BOOK: 45@1 (ingame), 48@3 (online). cumulative: (1, 4)
    assert snap.online_ask_depth == (1, 4)
    # online bids in BOOK: 40@2 (online), 30@9 (ingame). cumulative: (2, 11)
    assert snap.online_bid_depth == (2, 11)
    # the all-visible curves are unchanged
    assert snap.ask_depth == (2, 3, 6, 11)


def test_build_passes_the_online_depth_curves_through():
    features, _ = build(summarize(BOOK, slug="x", rank=0, ts=TS))
    assert features.online_ask_depth == (1, 4)
    assert features.online_bid_depth == (2, 11)


def test_online_depth_of_an_all_offline_side_is_empty():
    offline_only = [
        _o(44, 2, Side.SELL, status="offline", updated=OLD),
        _o(40, 2, Side.BUY, status="offline", updated=OLD),
    ]
    snap = summarize(offline_only, slug="x", rank=0, ts=TS)
    assert snap.online_ask_depth == ()
    assert snap.online_bid_depth == ()


def test_summarize_reads_the_side_enum_the_api_parser_actually_produces():
    """parse_orders builds Order.side as Side, and Side.SELL is not Direction.SELL.
    An identity check against the wrong enum would classify nothing and hand back an
    empty book for every real item.
    """
    from wfm.api.endpoints import parse_orders

    orders = parse_orders(
        [
            {"platinum": 45, "quantity": 1, "rank": 0, "type": "sell", "visible": True,
             "user": {"status": "ingame"}, "updatedAt": "2026-08-27T09:00:00Z"},
            {"platinum": 40, "quantity": 2, "rank": 0, "type": "buy", "visible": True,
             "user": {"status": "online"}, "updatedAt": "2026-08-27T09:00:00Z"},
        ],
        "x",
    )
    snap = summarize(orders, slug="x", rank=0, ts=TS)
    assert snap.best_ask == 45
    assert snap.best_bid == 40
