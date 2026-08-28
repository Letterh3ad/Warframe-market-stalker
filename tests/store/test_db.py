from datetime import datetime, timedelta, timezone

import pytest

from wfm.models import Item
from wfm.store.db import to_utc_iso, transaction
from wfm.store.groups import GroupsRepo
from wfm.store.items import ItemsRepo


def test_aware_non_utc_datetime_converts_to_utc_and_round_trips():
    ts = datetime(2026, 8, 27, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    iso = to_utc_iso(ts)
    assert iso == "2026-08-27T12:00:00+00:00"
    assert datetime.fromisoformat(iso) == ts


def test_naive_datetime_raises():
    with pytest.raises(ValueError):
        to_utc_iso(datetime(2026, 8, 27, 12, 0))


def test_nested_transaction_commits_both_repositories(conn):
    with transaction(conn):
        ItemsRepo(conn).upsert_many([Item(slug="nested", name="Nested", url_name="nested")])
        GroupsRepo(conn).create("g", datetime(2026, 8, 27, tzinfo=timezone.utc))
    assert ItemsRepo(conn).get("nested") is not None
    assert GroupsRepo(conn).get("g") is not None


def test_nested_transaction_rolls_back_both_repositories(conn):
    with pytest.raises(RuntimeError):
        with transaction(conn):
            ItemsRepo(conn).upsert_many([Item(slug="nested", name="Nested", url_name="nested")])
            GroupsRepo(conn).create("g", datetime(2026, 8, 27, tzinfo=timezone.utc))
            raise RuntimeError("boom")
    assert ItemsRepo(conn).get("nested") is None
    assert GroupsRepo(conn).get("g") is None


def test_inner_failure_rolls_back_only_the_inner_block(conn):
    with transaction(conn):
        ItemsRepo(conn).upsert_many([Item(slug="outer", name="Outer", url_name="outer")])
        with pytest.raises(RuntimeError):
            with transaction(conn):
                ItemsRepo(conn).upsert_many([Item(slug="inner", name="Inner", url_name="inner")])
                raise RuntimeError("boom")
    assert ItemsRepo(conn).get("outer") is not None
    assert ItemsRepo(conn).get("inner") is None
