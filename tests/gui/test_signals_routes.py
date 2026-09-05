from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.gui.app import build_app
from wfm.models import Direction, Horizon, Item, Signal
from wfm.services.context import AppContext

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _signal(analyzer: str, ts: datetime) -> Signal:
    return Signal(
        slug="x", rank=0, analyzer=analyzer, ts=ts, direction=Direction.BUY,
        magnitude=12.0, confidence=0.9, evidence={"fair_value": 52.5},
        horizon=Horizon.URGENT, expires_at=ts + timedelta(minutes=20),
    )


def _client(conn) -> TestClient:
    ctx = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    ctx.items.upsert_many([Item(slug="x", name="X", url_name="x")])
    ctx.signals.insert(_signal("flip", NOW))
    ctx.signals.insert(_signal("revert", NOW - timedelta(hours=1)))
    return TestClient(build_app(ctx))


def test_list_signals_returns_persisted_signals(conn):
    response = _client(conn).get("/signals")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert {s["analyzer"] for s in body} == {"flip", "revert"}


def test_list_signals_filters_by_analyzer(conn):
    response = _client(conn).get("/signals", params={"analyzer": "flip"})
    assert response.status_code == 200
    body = response.json()
    assert [s["analyzer"] for s in body] == ["flip"]


def test_list_signals_respects_the_limit(conn):
    response = _client(conn).get("/signals", params={"limit": 1})
    assert response.status_code == 200
    assert len(response.json()) == 1
