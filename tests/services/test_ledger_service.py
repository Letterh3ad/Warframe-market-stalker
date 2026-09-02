from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.models import BookSnapshot, Item
from wfm.services import ledger_service
from wfm.services.context import AppContext

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


@pytest.fixture
def ctx(conn):
    context = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    context.items.upsert_many(
        [Item(slug="x", name="Ex Item", url_name="x", max_rank=10, canonical_rank=10)]
    )
    return context


def test_record_resolves_a_name_and_defaults_to_the_canonical_rank(ctx):
    result = ledger_service.record(ctx, "buy", "Ex Item", quantity=2, platinum=40)
    assert result["slug"] == "x"
    assert result["rank"] == 10
    assert ctx.trades.all_for("x", 10)[0].quantity == 2


def test_a_non_positive_quantity_is_refused(ctx):
    with pytest.raises(ValueError):
        ledger_service.record(ctx, "buy", "x", quantity=0, platinum=40)


def test_record_refuses_an_ambiguous_name_rather_than_guessing(ctx):
    ctx.items.upsert_many([
        Item(slug="continuity", name="Continuity", url_name="continuity"),
        Item(slug="primed_continuity", name="Primed Continuity", url_name="primed_continuity"),
    ])
    with pytest.raises(ValueError, match="ambiguous"):
        ledger_service.record(ctx, "buy", "continu", quantity=1, platinum=40)


def test_record_takes_an_exact_name_even_with_other_partial_matches(ctx):
    ctx.items.upsert_many([
        Item(slug="continuity", name="Continuity", url_name="continuity"),
        Item(slug="primed_continuity", name="Primed Continuity", url_name="primed_continuity"),
    ])
    result = ledger_service.record(ctx, "buy", "Continuity", quantity=1, platinum=40)
    assert result["slug"] == "continuity"


def test_selling_more_than_held_is_refused(ctx):
    ledger_service.record(ctx, "buy", "x", quantity=1, platinum=40)
    with pytest.raises(ValueError) as excinfo:
        ledger_service.record(ctx, "sell", "x", quantity=5, platinum=60)
    assert "hold" in str(excinfo.value).lower()


def test_holdings_are_derived_not_stored(ctx):
    ledger_service.record(ctx, "buy", "x", quantity=4, platinum=40)
    ledger_service.record(ctx, "sell", "x", quantity=1, platinum=70)
    holdings = ledger_service.holdings(ctx)
    assert holdings[0]["quantity"] == 3
    assert holdings[0]["avg_cost"] == 40.0
    assert holdings[0]["name"] == "Ex Item"


def test_holdings_are_marked_to_the_last_stored_book(ctx):
    ledger_service.record(ctx, "buy", "x", quantity=2, platinum=40)
    ctx.orders.insert(BookSnapshot(slug="x", rank=10, ts=NOW, online_best_bid=55, best_bid=55))
    holdings = ledger_service.holdings(ctx)
    assert holdings[0]["mark"] == 55
    assert holdings[0]["unrealized_profit"] == pytest.approx(30.0)


def test_pnl_reports_realized_and_unrealized(ctx):
    ledger_service.record(ctx, "buy", "x", quantity=4, platinum=40)
    ledger_service.record(ctx, "sell", "x", quantity=2, platinum=70)
    report = ledger_service.pnl(ctx)
    assert report["realized_profit"] == 60
    assert len(report["open_positions"]) == 1


def test_pnl_since_keeps_the_cost_basis_of_a_buy_that_predates_the_window(ctx):
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    mid = datetime(2026, 2, 1, tzinfo=timezone.utc)
    ledger_service.record(ctx, "buy", "x", quantity=2, platinum=40, when=old)
    ledger_service.record(ctx, "sell", "x", quantity=2, platinum=70, when=mid)
    report = ledger_service.pnl(ctx, since=datetime(2026, 1, 15, tzinfo=timezone.utc))
    # The sale is in the window; its buy is not. Realized profit is still (70-40)*2.
    assert report["realized_profit"] == 60
    assert len(report["lots"]) == 1


def test_pnl_since_excludes_a_lot_sold_before_the_window(ctx):
    ledger_service.record(ctx, "buy", "x", quantity=2, platinum=40,
                          when=datetime(2026, 1, 1, tzinfo=timezone.utc))
    ledger_service.record(ctx, "sell", "x", quantity=2, platinum=70,
                          when=datetime(2026, 1, 2, tzinfo=timezone.utc))
    report = ledger_service.pnl(ctx, since=datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert report["realized_profit"] == 0
    assert report["lots"] == []
