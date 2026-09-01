from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.models import Item
from wfm.services import group_service
from wfm.services.context import AppContext

START = datetime(2026, 8, 27, tzinfo=timezone.utc)


@pytest.fixture
def ctx(conn):
    context = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=START))
    context.items.upsert_many(
        [
            Item(slug="a", name="Alpha", url_name="a"),
            Item(slug="b", name="Beta", url_name="b", max_rank=10, canonical_rank=10),
        ]
    )
    return context


def test_new_and_ls(ctx):
    group_service.new(ctx, "primes")
    assert [g["name"] for g in group_service.ls(ctx)] == ["primes"]


def test_duplicate_group_is_a_readable_error(ctx):
    group_service.new(ctx, "primes")
    with pytest.raises(ValueError):
        group_service.new(ctx, "primes")


def test_membership_is_independent_of_the_watchlist(ctx):
    group_service.new(ctx, "primes")
    group_service.add(ctx, "primes", "b")
    shown = group_service.show(ctx, "primes")
    assert shown["members"] == [{"slug": "b", "rank": 10, "name": "Beta"}]
    assert ctx.watchlist.all() == []


def test_remove_member_and_delete_group(ctx):
    group_service.new(ctx, "primes")
    group_service.add(ctx, "primes", "a")
    assert group_service.remove(ctx, "primes", "a")["removed"] is True
    assert group_service.rm(ctx, "primes")["removed"] is True


def test_remove_all_ranks_removes_every_rank_not_just_the_first(ctx):
    group_service.new(ctx, "primes")
    group_service.add(ctx, "primes", "b", rank="all")
    group_service.remove(ctx, "primes", "b", rank="all")
    assert group_service.show(ctx, "primes")["members"] == []
