import logging
from datetime import datetime, timezone

import pytest

from tests.fakes.api import StubClient
from tests.fakes.clock import FakeClock
from wfm.store.items import ItemsRepo
from wfm.store.sweep import SweepStateRepo
from wfm.sync.catalog import sync_catalog

START = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)

VERSIONS = {"apiVersion": "2.0", "collections": {"items": "v42"}}
ITEMS = [
    {"slug": "mirage_prime_set", "i18n": {"en": {"name": "Mirage Prime Set"}}, "tags": ["set"]},
    {
        "slug": "primed_continuity",
        "i18n": {"en": {"name": "Primed Continuity"}},
        "tags": ["mod"],
        "maxRank": 10,
    },
    {"slug": "riven_thing", "i18n": {"en": {"name": "Riven Thing"}}, "tags": ["riven"]},
]


def _deps(conn):
    return ItemsRepo(conn), SweepStateRepo(conn), FakeClock(start_utc=START)


async def test_first_sync_stores_items_and_the_version(conn):
    items, sweeps, clock = _deps(conn)
    client = StubClient({"/versions": VERSIONS, "/items": ITEMS})
    result = await sync_catalog(client, items, sweeps, clock)
    assert result.changed is True
    assert result.version == "v42"
    assert result.item_count == 2
    assert items.get("primed_continuity").canonical_rank == 10
    assert items.get("riven_thing") is None
    assert result.requests_spent == 2


async def test_unchanged_version_costs_one_request(conn):
    items, sweeps, clock = _deps(conn)
    client = StubClient({"/versions": VERSIONS, "/items": ITEMS})
    await sync_catalog(client, items, sweeps, clock)
    client.calls.clear()
    result = await sync_catalog(client, items, sweeps, clock)
    assert result.changed is False
    assert result.requests_spent == 1
    assert len(client.calls) == 1
    assert items.count() == 2


async def test_force_refetches_even_when_the_version_is_unchanged(conn):
    items, sweeps, clock = _deps(conn)
    client = StubClient({"/versions": VERSIONS, "/items": ITEMS})
    await sync_catalog(client, items, sweeps, clock)
    result = await sync_catalog(client, items, sweeps, clock, force=True)
    assert result.changed is True
    assert result.requests_spent == 2


async def test_a_moved_version_triggers_a_refetch(conn):
    items, sweeps, clock = _deps(conn)
    client = StubClient({"/versions": VERSIONS, "/items": ITEMS})
    await sync_catalog(client, items, sweeps, clock)
    moved = {"apiVersion": "2.0", "collections": {"items": "v43"}}
    client = StubClient({"/versions": moved, "/items": ITEMS})
    result = await sync_catalog(client, items, sweeps, clock)
    assert result.changed is True
    assert items.get("primed_continuity").last_seen_version == "v43"


async def test_the_item_write_and_the_cursor_move_together_or_not_at_all(conn, monkeypatch):
    items, sweeps, clock = _deps(conn)
    client = StubClient({"/versions": VERSIONS, "/items": ITEMS})

    def boom(*args, **kwargs):
        raise RuntimeError("disk full mid-commit")

    monkeypatch.setattr(sweeps, "checkpoint", boom)
    with pytest.raises(RuntimeError):
        await sync_catalog(client, items, sweeps, clock)
    # The cursor write failed, so the item rows written in the same block roll back.
    assert items.count() == 0
    assert sweeps.get("catalog")["cursor"] is None


async def test_an_unparseable_version_token_is_logged_and_forces_a_refetch(conn, caplog):
    items, sweeps, clock = _deps(conn)
    client = StubClient({"/versions": {"unexpected": "shape"}, "/items": ITEMS})
    with caplog.at_level(logging.WARNING, logger="wfm.sync.catalog"):
        result = await sync_catalog(client, items, sweeps, clock)
    assert result.changed is True
    assert result.version is None
    assert "version token" in caplog.text
