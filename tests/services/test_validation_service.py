import pytest

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.models import DailyCandle, Item
from wfm.services.context import AppContext
from wfm.services import validation_service

NOW = __import__("datetime").datetime(2026, 8, 27, tzinfo=__import__("datetime").timezone.utc)


@pytest.fixture
def ctx(conn):
    context = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    context.items.upsert_many([Item(slug="x", name="X", url_name="x", tags=("mod",))])
    from datetime import date, timedelta

    base = date(2026, 6, 1)
    context.daily.upsert_many(
        [
            DailyCandle(slug="x", rank=0, date=(base + timedelta(days=i)).isoformat(),
                        close=50, high=51, low=49, median=50, volume=20)
            for i in range(40)
        ]
    )
    return context


def test_validate_with_no_explicit_analyzer_excludes_group_scoped_analyzers(ctx):
    rows = validation_service.validate(ctx, start="2026-06-20", end="2026-06-30")
    assert "set_arbitrage" not in {r["analyzer"] for r in rows}


def test_validate_with_explicit_group_analyzer_still_raises(ctx):
    with pytest.raises(ValueError, match="not an ITEM-scoped analyzer"):
        validation_service.validate(
            ctx, start="2026-06-20", end="2026-06-30", analyzer="set_arbitrage"
        )
