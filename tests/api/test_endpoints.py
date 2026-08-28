from datetime import datetime, timezone

import pytest

from wfm.api.endpoints import (
    orders_url,
    parse_items,
    parse_orders,
    parse_statistics,
    statistics_url,
)
from wfm.models import Side

ITEMS_PAYLOAD = [
    {
        "slug": "mirage_prime_set",
        "i18n": {"en": {"name": "Mirage Prime Set"}},
        "tags": ["set", "prime"],
        "maxRank": None,
        "ducats": 0,
    },
    {
        "slug": "primed_continuity",
        "i18n": {"en": {"name": "Primed Continuity"}},
        "tags": ["mod", "primed"],
        "maxRank": 10,
    },
    {
        "slug": "some_riven_mod",
        "i18n": {"en": {"name": "Some Riven Mod"}},
        "tags": ["riven"],
    },
]

ORDERS_PAYLOAD = [
    {
        "platinum": 40,
        "quantity": 2,
        "rank": 0,
        "type": "sell",
        "visible": True,
        "user": {"status": "ingame"},
        "updatedAt": "2026-08-27T09:00:00Z",
    },
    {
        "platinum": 30,
        "quantity": 1,
        "rank": 0,
        "type": "buy",
        "visible": True,
        "user": {"status": "offline"},
        "updatedAt": "2024-01-02T09:00:00Z",
    },
    {
        "platinum": 999,
        "quantity": 1,
        "rank": 0,
        "type": "sell",
        "visible": False,
        "user": {"status": "online"},
    },
]

STATS_PAYLOAD = {
    "payload": {
        "statistics_closed": {
            "90days": [
                {
                    "datetime": "2026-08-26T00:00:00.000+00:00",
                    "volume": 12,
                    "min_price": 35,
                    "max_price": 55,
                    "open_price": 40,
                    "closed_price": 44,
                    "avg_price": 43.2,
                    "wa_price": 43.9,
                    "median": 42,
                    "moving_avg": 41.5,
                    "donch_top": 55,
                    "donch_bot": 35,
                    "mod_rank": 0,
                },
                {
                    "datetime": "2026-08-26T00:00:00.000+00:00",
                    "volume": 3,
                    "min_price": 300,
                    "max_price": 340,
                    "open_price": 310,
                    "closed_price": 330,
                    "avg_price": 322.0,
                    "median": 325,
                    "mod_rank": 10,
                },
            ],
            "48hours": [
                {
                    "datetime": "2026-08-27T09:00:00.000+00:00",
                    "volume": 2,
                    "min_price": 41,
                    "max_price": 46,
                    "open_price": 42,
                    "closed_price": 45,
                    "avg_price": 43.5,
                    "median": 43,
                    "mod_rank": 0,
                }
            ],
        }
    }
}


def test_urls():
    assert orders_url("primed_continuity").endswith("/v2/orders/item/primed_continuity")
    assert statistics_url("primed_continuity").endswith("/v1/items/primed_continuity/statistics")


def test_parse_items_maps_names_tags_and_ranks():
    items = {i.slug: i for i in parse_items(ITEMS_PAYLOAD)}
    assert items["mirage_prime_set"].name == "Mirage Prime Set"
    assert items["mirage_prime_set"].is_set is True
    assert items["mirage_prime_set"].max_rank == 0
    assert items["primed_continuity"].max_rank == 10
    assert items["primed_continuity"].tags == ("mod", "primed")


def test_parse_items_excludes_rivens():
    assert "some_riven_mod" not in {i.slug for i in parse_items(ITEMS_PAYLOAD)}


def test_canonical_rank_is_max_rank_for_ranked_items():
    items = {i.slug: i for i in parse_items(ITEMS_PAYLOAD)}
    assert items["primed_continuity"].canonical_rank == 10
    assert items["mirage_prime_set"].canonical_rank == 0


def test_parse_orders_reads_side_status_and_visibility():
    orders = parse_orders(ORDERS_PAYLOAD, slug="x")
    assert len(orders) == 3
    sell = orders[0]
    assert sell.side is Side.SELL
    assert sell.platinum == 40
    assert sell.is_online is True
    assert sell.updated_at == datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)
    assert orders[1].is_online is False
    assert orders[2].visible is False
    assert orders[2].updated_at is None


def test_parse_statistics_splits_by_rank_and_granularity():
    daily, hourly = parse_statistics(STATS_PAYLOAD, slug="primed_continuity")
    by_rank = {c.rank: c for c in daily}
    assert by_rank[0].date == "2026-08-26"
    assert by_rank[0].close == 44
    assert by_rank[0].high == 55
    assert by_rank[0].low == 35
    assert by_rank[0].moving_avg == 41.5
    assert by_rank[10].close == 330
    assert by_rank[10].moving_avg is None
    assert len(hourly) == 1
    assert hourly[0].ts == datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)
    assert hourly[0].rank == 0


def test_parse_statistics_of_an_item_with_no_trades_is_empty():
    daily, hourly = parse_statistics({"payload": {"statistics_closed": {}}}, slug="x")
    assert daily == []
    assert hourly == []


def test_a_candle_without_a_timestamp_is_skipped_not_fatal():
    payload = {
        "payload": {
            "statistics_closed": {
                "90days": [
                    {"volume": 1, "closed_price": 10, "mod_rank": 0},
                    {"datetime": None, "volume": 1, "mod_rank": 0},
                    {"datetime": "2026-08-26T00:00:00.000+00:00", "volume": 2, "mod_rank": 0},
                ]
            }
        }
    }
    daily, _ = parse_statistics(payload, slug="x")
    assert [c.date for c in daily] == ["2026-08-26"]


def test_a_null_i18n_block_falls_back_to_the_slug():
    items = parse_items([{"slug": "x", "i18n": None}, {"slug": "y", "i18n": {"en": None}}])
    assert [i.name for i in items] == ["x", "y"]
