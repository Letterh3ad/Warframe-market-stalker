from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.models import Item
from wfm.services import catalog_service
from wfm.services.context import AppContext

START = datetime(2026, 8, 27, tzinfo=timezone.utc)


@pytest.fixture
def ctx(conn):
    context = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=START))
    context.items.upsert_many(
        [
            Item(slug="mirage_prime_set", name="Mirage Prime Set", url_name="mirage_prime_set",
                 tags=("set",), is_set=True),
            Item(slug="primed_continuity", name="Primed Continuity", url_name="primed_continuity",
                 tags=("mod",), max_rank=10, canonical_rank=10),
        ]
    )
    return context


def test_search_returns_plain_dicts_grouped_by_tag(ctx):
    results = catalog_service.search(ctx, "prime")
    assert isinstance(results[0], dict)
    assert {r["slug"] for r in results} == {"mirage_prime_set", "primed_continuity"}
    assert results[0]["tags"] == ["set"] or results[0]["tags"] == ["mod"]


def test_resolve_defaults_to_the_canonical_rank(ctx):
    assert catalog_service.resolve(ctx, "primed_continuity") == ("primed_continuity", [10])
    assert catalog_service.resolve(ctx, "mirage_prime_set") == ("mirage_prime_set", [0])


def test_resolve_accepts_an_explicit_rank_and_all(ctx):
    ctx.daily.upsert_many([])
    assert catalog_service.resolve(ctx, "primed_continuity", rank=0) == ("primed_continuity", [0])
    slug, ranks = catalog_service.resolve(ctx, "primed_continuity", rank="all")
    assert slug == "primed_continuity"
    assert ranks == list(range(0, 11))


def test_resolve_matches_a_display_name(ctx):
    assert catalog_service.resolve(ctx, "Primed Continuity")[0] == "primed_continuity"


def test_resolve_raises_a_readable_error_when_ambiguous_or_missing(ctx):
    with pytest.raises(LookupError) as excinfo:
        catalog_service.resolve(ctx, "nonsense")
    assert "nonsense" in str(excinfo.value)


@pytest.fixture
def bare_ctx(conn):
    """The shared `ctx` fixture pre-loads two items; browse assertions here count rows,
    so they need an empty catalog to start from."""
    return AppContext(Config(), conn=conn, clock=FakeClock(start_utc=START))


def test_browse_pages_and_reports_the_unpaged_total(bare_ctx):
    bare_ctx.items.upsert_many([
        Item(slug=f"i{n}", name=f"Item {n:02d}", url_name=f"i{n}") for n in range(10)
    ])
    page = catalog_service.browse(bare_ctx, limit=3, offset=3)
    assert page["total"] == 10
    assert page["limit"] == 3
    assert page["offset"] == 3
    assert [i["name"] for i in page["items"]] == ["Item 03", "Item 04", "Item 05"]


def test_browse_clamps_the_limit_and_floors_a_negative_offset(bare_ctx):
    bare_ctx.items.upsert_many([Item(slug="a", name="A", url_name="a")])
    assert catalog_service.browse(bare_ctx, limit=99999)["limit"] == catalog_service.MAX_BROWSE_LIMIT
    assert catalog_service.browse(bare_ctx, limit=0)["limit"] == 1
    assert catalog_service.browse(bare_ctx, offset=-5)["offset"] == 0
