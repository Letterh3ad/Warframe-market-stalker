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
    ctx.items.upsert_many([Item(slug="x", name="X", url_name="x")])
    client = TestClient(build_app(ctx))
    client.ctx = ctx
    return client


def test_list_watchlist_is_empty_by_default(conn):
    assert _client(conn).get("/watchlist").json() == []


def test_add_and_list_a_watchlist_entry(conn):
    client = _client(conn)
    response = client.post("/watchlist", json={"query": "x", "pin": 2.0})
    assert response.status_code == 200
    assert response.json()["slug"] == "x"
    listed = client.get("/watchlist").json()
    assert len(listed) == 1
    assert listed[0]["slug"] == "x"
    assert listed[0]["pin_weight"] == 2.0


def test_remove_a_watchlist_entry(conn):
    client = _client(conn)
    client.post("/watchlist", json={"query": "x"})
    response = client.delete("/watchlist/x/0")
    assert response.status_code == 200
    assert response.json()["removed"] == 1
    assert client.get("/watchlist").json() == []


def test_an_unresolvable_query_is_a_404(conn):
    response = _client(conn).post("/watchlist", json={"query": "no such item"})
    assert response.status_code == 404
