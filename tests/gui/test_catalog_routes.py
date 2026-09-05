from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.gui.app import build_app
from wfm.models import Item
from wfm.services.context import AppContext

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _client(conn) -> TestClient:
    ctx = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    ctx.items.upsert_many([
        Item(slug=f"i{n}", name=f"Item {n:02d}", url_name=f"i{n}") for n in range(10)
    ])
    return TestClient(build_app(ctx))


def test_browse_returns_a_page_and_a_total(conn):
    response = _client(conn).get("/catalog", params={"limit": 4})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 10
    assert len(body["items"]) == 4
    assert body["items"][0]["slug"] == "i0"


def test_browse_filters_by_query(conn):
    response = _client(conn).get("/catalog", params={"q": "Item 07"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["slug"] == "i7"


def test_an_offset_past_the_end_is_an_empty_page_with_a_correct_total(conn):
    response = _client(conn).get("/catalog", params={"offset": 500})
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 10
