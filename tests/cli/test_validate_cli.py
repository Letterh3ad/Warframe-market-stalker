import json

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


def test_validate_returns_one_row_per_enabled_analyzer(ctx):
    rows = validation_service.validate(ctx, start="2026-06-20", end="2026-06-30")
    assert {r["analyzer"] for r in rows} == {"flip", "revert", "selltime"}


def test_validate_sweep_returns_one_row_per_value(ctx):
    rows = validation_service.validate(
        ctx, start="2026-06-20", end="2026-06-30", analyzer="revert",
        sweep_key="z_threshold", sweep_values=[1.0, 2.0],
    )
    assert [r["z_threshold"] for r in rows] == [1.0, 2.0]
