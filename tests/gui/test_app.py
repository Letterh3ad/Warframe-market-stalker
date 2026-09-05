from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.gui.app import build_app
from wfm.services.context import AppContext

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def test_build_app_exposes_the_context_on_app_state(conn):
    ctx = AppContext(Config(), conn=conn)
    app = build_app(ctx)
    assert app.state.ctx is ctx


def test_the_index_page_is_served_at_the_root(conn):
    ctx = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    response = TestClient(build_app(ctx)).get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_the_api_routers_are_not_shadowed_by_static_serving(conn):
    ctx = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    response = TestClient(build_app(ctx)).get("/catalog")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
