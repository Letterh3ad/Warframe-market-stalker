from __future__ import annotations

from datetime import datetime
from typing import Any

from wfm.models import DailyCandle, HourlyCandle, Item, Order, Side
from wfm.sync.budget import Priority

V1_BASE = "https://api.warframe.market/v1"
V2_BASE = "https://api.warframe.market/v2"

# Excluded here rather than in the sync layer so no caller can accidentally persist one.
EXCLUDED_TAGS = frozenset({"riven"})


def versions_url() -> str:
    return f"{V2_BASE}/versions"


def items_url() -> str:
    return f"{V2_BASE}/items"


def orders_url(slug: str) -> str:
    return f"{V2_BASE}/orders/item/{slug}"


def statistics_url(slug: str) -> str:
    return f"{V1_BASE}/items/{slug}/statistics"


def _pick(source: dict, *names: str, default: Any = None) -> Any:
    """v2 has been observed using camelCase where v1 uses snake_case, so a rename
    upstream should degrade to a missing field rather than a crash."""
    for name in names:
        if name in source and source[name] is not None:
            return source[name]
    return default


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_items(payload: Any) -> list[Item]:
    raw = payload.get("items", []) if isinstance(payload, dict) else payload
    items: list[Item] = []
    for entry in raw:
        tags = tuple(_pick(entry, "tags", default=()) or ())
        if EXCLUDED_TAGS & set(tags):
            continue
        slug = _pick(entry, "slug", "urlName", "url_name")
        if not slug:
            continue
        # `or {}` rather than a get() default: the default only covers a missing key,
        # and a present-but-null i18n block is exactly what tolerance is for.
        english = (entry.get("i18n") or {}).get("en") or {}
        name = english.get("name") or _pick(entry, "name", "item_name", default=slug)
        max_rank = int(_pick(entry, "maxRank", "max_rank", default=0) or 0)
        items.append(
            Item(
                slug=slug,
                name=name,
                url_name=_pick(entry, "urlName", "url_name", default=slug),
                tags=tags,
                max_rank=max_rank,
                canonical_rank=max_rank,
                ducats=_pick(entry, "ducats"),
                is_set="set" in tags,
            )
        )
    return items


def parse_orders(payload: Any, slug: str) -> list[Order]:
    raw = payload.get("orders", []) if isinstance(payload, dict) else payload
    orders: list[Order] = []
    for entry in raw:
        side_raw = str(_pick(entry, "type", "order_type", default="sell")).lower()
        user = _pick(entry, "user", default={}) or {}
        orders.append(
            Order(
                platinum=int(_pick(entry, "platinum", default=0) or 0),
                quantity=int(_pick(entry, "quantity", default=0) or 0),
                rank=int(_pick(entry, "rank", "mod_rank", default=0) or 0),
                side=Side.SELL if side_raw == "sell" else Side.BUY,
                visible=bool(_pick(entry, "visible", default=True)),
                user_status=str(_pick(user, "status", default="offline")).lower(),
                updated_at=_parse_ts(_pick(entry, "updatedAt", "last_update", "updated_at")),
            )
        )
    return orders


def parse_statistics(payload: Any, slug: str) -> tuple[list[DailyCandle], list[HourlyCandle]]:
    closed = payload.get("payload", payload).get("statistics_closed", {})
    daily = [c for c in map(lambda e: _to_daily(e, slug), closed.get("90days", [])) if c]
    hourly = [c for c in map(lambda e: _to_hourly(e, slug), closed.get("48hours", [])) if c]
    return daily, hourly


def _to_daily(entry: dict, slug: str) -> DailyCandle | None:
    ts = _parse_ts(_pick(entry, "datetime"))
    if ts is None:
        return None
    return DailyCandle(
        slug=slug,
        rank=int(_pick(entry, "mod_rank", default=0) or 0),
        date=ts.date().isoformat(),
        volume=_pick(entry, "volume"),
        open=_pick(entry, "open_price"),
        high=_pick(entry, "max_price"),
        low=_pick(entry, "min_price"),
        close=_pick(entry, "closed_price"),
        median=_pick(entry, "median"),
        avg_price=_pick(entry, "avg_price"),
        wa_price=_pick(entry, "wa_price"),
        moving_avg=_pick(entry, "moving_avg"),
        donch_top=_pick(entry, "donch_top"),
        donch_bot=_pick(entry, "donch_bot"),
    )


def _to_hourly(entry: dict, slug: str) -> HourlyCandle | None:
    ts = _parse_ts(_pick(entry, "datetime"))
    if ts is None:
        return None
    return HourlyCandle(
        slug=slug,
        rank=int(_pick(entry, "mod_rank", default=0) or 0),
        ts=ts,
        volume=_pick(entry, "volume"),
        open=_pick(entry, "open_price"),
        high=_pick(entry, "max_price"),
        low=_pick(entry, "min_price"),
        close=_pick(entry, "closed_price"),
        median=_pick(entry, "median"),
        avg_price=_pick(entry, "avg_price"),
        wa_price=_pick(entry, "wa_price"),
    )


async def fetch_versions(client, priority: Priority = Priority.BULK) -> dict:
    return await client.get_json(versions_url(), priority=priority, use_cache=True)


async def fetch_items(client, priority: Priority = Priority.BULK) -> list[Item]:
    return parse_items(await client.get_json(items_url(), priority=priority, use_cache=True))


async def fetch_orders(client, slug: str, priority: Priority = Priority.BACKGROUND) -> list[Order]:
    return parse_orders(await client.get_json(orders_url(slug), priority=priority), slug)


async def fetch_statistics(
    client, slug: str, priority: Priority = Priority.BULK
) -> tuple[list[DailyCandle], list[HourlyCandle]]:
    payload = await client.get_json(statistics_url(slug), priority=priority)
    return parse_statistics(payload, slug)
