from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.gui.app import build_app
from wfm.models import BookSnapshot, Item
from wfm.services.context import AppContext

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _client(conn) -> TestClient:
    ctx = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    ctx.items.upsert_many(
        [
            Item(slug="frame_prime_set", name="Frame Prime Set", url_name="frame-prime-set"),
            Item(
                slug="frame_prime_chassis_blueprint", name="Frame Prime Chassis Blueprint",
                url_name="frame-prime-chassis-blueprint",
            ),
        ]
    )
    client = TestClient(build_app(ctx))
    client.ctx = ctx
    return client


def test_create_add_a_member_and_show_a_group(conn):
    client = _client(conn)
    assert client.post("/groups", json={"name": "frame"}).status_code == 200
    assert client.get("/groups").json() == [{"name": "frame", "members": 0}]

    client.post("/groups/frame/members", json={"query": "frame_prime_set", "rank": 0})
    shown = client.get("/groups/frame").json()
    assert shown["name"] == "frame"
    assert len(shown["members"]) == 1
    assert shown["members"][0]["slug"] == "frame_prime_set"


def test_removing_a_member(conn):
    client = _client(conn)
    client.post("/groups", json={"name": "frame"})
    client.post("/groups/frame/members", json={"query": "frame_prime_set", "rank": 0})
    response = client.request(
        "DELETE", "/groups/frame/members", json={"query": "frame_prime_set", "rank": 0}
    )
    assert response.status_code == 200
    assert response.json()["removed"] is True


def test_deleting_a_group(conn):
    client = _client(conn)
    client.post("/groups", json={"name": "frame"})
    assert client.delete("/groups/frame").json()["removed"] is True
    assert client.get("/groups").json() == []


def test_showing_a_missing_group_is_a_404(conn):
    assert _client(conn).get("/groups/nope").status_code == 404


def test_analysis_surfaces_the_set_arbitrage_signal(conn):
    client = _client(conn)
    client.post("/groups", json={"name": "frame"})
    client.post("/groups/frame/members", json={"query": "frame_prime_set", "rank": 0})
    client.post(
        "/groups/frame/members", json={"query": "frame_prime_chassis_blueprint", "rank": 0}
    )
    client.ctx.orders.insert(
        BookSnapshot(slug="frame_prime_set", rank=0, ts=NOW, online_best_ask=80)
    )
    client.ctx.orders.insert(
        BookSnapshot(slug="frame_prime_chassis_blueprint", rank=0, ts=NOW, online_best_bid=100)
    )

    response = client.get("/groups/frame/analysis")

    assert response.status_code == 200
    body = response.json()
    assert len(body["group_signals"]) == 1
    assert body["group_signals"][0]["analyzer"] == "set_arbitrage"
    assert body["group_signals"][0]["direction"] == "buy"
