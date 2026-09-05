from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.gui.app import build_app
from wfm.models import DailyCandle, Item
from wfm.services.context import AppContext

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _client(conn) -> TestClient:
    ctx = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    ctx.items.upsert_many([Item(slug="x", name="X", url_name="x")])
    ctx.daily.upsert_many(
        [
            DailyCandle(slug="x", rank=0, date=f"2026-08-{d:02d}", close=50, high=52, low=48,
                        median=50, volume=30)
            for d in range(1, 29)
        ]
    )
    return TestClient(build_app(ctx))


def test_get_item_reports_price_history(conn):
    response = _client(conn).get("/items/x")
    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == "x"
    assert body["name"] == "X"
    assert body["price"]["median_30d"] is not None


def test_get_item_with_an_unresolvable_slug_is_a_404(conn):
    response = _client(conn).get("/items/does-not-exist")
    assert response.status_code == 404
