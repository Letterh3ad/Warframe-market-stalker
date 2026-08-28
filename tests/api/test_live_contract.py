"""Hits the real API a handful of times. Excluded by default.

Run manually with: pytest -m live tests/api/test_live_contract.py -v
"""

import pytest

from wfm.api.breaker import CircuitBreaker
from wfm.api.client import WFMClient
from wfm.api.endpoints import fetch_items, fetch_orders, fetch_statistics, fetch_versions
from wfm.api.ratelimit import TokenBucket
from wfm.clock import SystemClock
from wfm.config import Config
from wfm.sync.budget import Budget

SLUG = "primed_continuity"

pytestmark = pytest.mark.live


@pytest.fixture
async def client():
    config = Config()
    clock = SystemClock()
    budget = Budget(TokenBucket(config.requests_per_second, clock), clock)
    async with WFMClient(config, budget, CircuitBreaker(clock=clock), clock) as c:
        yield c


async def test_versions_endpoint_answers(client):
    assert isinstance(await fetch_versions(client), (dict, list))


async def test_catalog_parses_and_excludes_rivens(client):
    items = await fetch_items(client)
    assert len(items) > 3000
    assert all("riven" not in i.tags for i in items)
    assert any(i.slug == SLUG for i in items)


async def test_order_book_is_the_full_book(client):
    orders = await fetch_orders(client, SLUG)
    assert len(orders) > 20
    assert any(o.is_online for o in orders)
    assert all(o.platinum > 0 for o in orders)


async def test_statistics_return_ranked_daily_and_hourly_candles(client):
    daily, hourly = await fetch_statistics(client, SLUG)
    assert len(daily) > 30
    assert {c.rank for c in daily} >= {0, 10}
    assert all(c.volume is not None for c in daily)
    assert len(hourly) > 0
