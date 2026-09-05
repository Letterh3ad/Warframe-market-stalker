from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from wfm.models import Item
from wfm.store.db import transaction

_COLUMNS = (
    "slug, name, url_name, tags, max_rank, canonical_rank, ducats, is_set, last_seen_version"
)


class ItemsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_many(self, items: Iterable[Item]) -> int:
        """Whole-row replace: pass complete items, since an omitted column nulls the
        stored value. Only catalog sync and backfill call this, from a full API payload.
        """
        rows = [
            (
                item.slug,
                item.name,
                item.url_name,
                json.dumps(list(item.tags)),
                item.max_rank,
                item.canonical_rank,
                item.ducats,
                int(item.is_set),
                item.last_seen_version,
            )
            for item in items
        ]
        with transaction(self._conn):
            self._conn.executemany(
                f"INSERT INTO items ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(slug) DO UPDATE SET "
                "name=excluded.name, url_name=excluded.url_name, tags=excluded.tags, "
                "max_rank=excluded.max_rank, canonical_rank=excluded.canonical_rank, "
                "ducats=excluded.ducats, is_set=excluded.is_set, "
                "last_seen_version=excluded.last_seen_version",
                rows,
            )
        return len(rows)

    def get(self, slug: str) -> Item | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM items WHERE slug=?", (slug,)
        ).fetchone()
        return _to_item(row) if row else None

    def all_slugs(self) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT slug FROM items ORDER BY slug")]

    def search(self, query: str, limit: int = 20) -> list[Item]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM items WHERE name LIKE ? ESCAPE '\\' ORDER BY name LIMIT ?",
            (f"%{_escape_like(query)}%", limit),
        )
        return [_to_item(r) for r in rows]

    def page(
        self, query: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[Item]:
        if query:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM items WHERE name LIKE ? ESCAPE '\\' "
                "ORDER BY name LIMIT ? OFFSET ?",
                (f"%{_escape_like(query)}%", limit, offset),
            )
        else:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM items ORDER BY name LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [_to_item(r) for r in rows]

    def canonical_rank(self, slug: str) -> int:
        """Returns 0 for a slug not yet in the catalog. Callers that may be handed an
        unsynced slug must check existence separately rather than trusting this value.
        """
        row = self._conn.execute(
            "SELECT canonical_rank FROM items WHERE slug=?", (slug,)
        ).fetchone()
        return int(row[0]) if row else 0

    def count(self, query: str | None = None) -> int:
        if query:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM items WHERE name LIKE ? ESCAPE '\\'",
                (f"%{_escape_like(query)}%",),
            ).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) FROM items").fetchone()
        return int(row[0])


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_item(row: sqlite3.Row) -> Item:
    return Item(
        slug=row["slug"],
        name=row["name"],
        url_name=row["url_name"],
        tags=tuple(json.loads(row["tags"])),
        max_rank=row["max_rank"],
        canonical_rank=row["canonical_rank"],
        ducats=row["ducats"],
        is_set=bool(row["is_set"]),
        last_seen_version=row["last_seen_version"],
    )
