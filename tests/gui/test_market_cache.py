from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.gui import market_cache
from wfm.gui.app import build_app
from wfm.models import Item
from wfm.services import feature_service
from wfm.services.context import AppContext

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def test_the_market_context_is_built_once_within_the_ttl(conn, monkeypatch):
    ctx = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    ctx.items.upsert_many([Item(slug="x", name="X", url_name="x")])
    builds = []
    real = feature_service.market_context

    def counting(c, now=None):
        builds.append(now)
        return real(c, now=now)

    monkeypatch.setattr(feature_service, "market_context", counting)
    client = TestClient(build_app(ctx))
    client.get("/items/x")
    client.get("/items/x")
    assert len(builds) == 1


def test_the_market_context_is_rebuilt_once_the_ttl_expires(conn, monkeypatch):
    clock = FakeClock(start_utc=NOW)
    ctx = AppContext(Config(), conn=conn, clock=clock)
    ctx.items.upsert_many([Item(slug="x", name="X", url_name="x")])
    builds = []
    real = feature_service.market_context
    monkeypatch.setattr(
        feature_service, "market_context",
        lambda c, now=None: (builds.append(now), real(c, now=now))[1],
    )
    client = TestClient(build_app(ctx))
    client.get("/items/x")
    clock.advance(seconds=market_cache.MARKET_TTL_S + 1)
    client.get("/items/x")
    assert len(builds) == 2
