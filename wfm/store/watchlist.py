from __future__ import annotations

import sqlite3
from datetime import datetime

from wfm.models import WatchlistEntry
from wfm.store.db import to_utc_iso, transaction

_COLS = 'slug, "rank", added_at, pin_weight, alert_override'


class WatchlistRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(
        self,
        slug: str,
        rank: int,
        added_at: datetime,
        pin_weight: float = 0.0,
        alert_override: bool = False,
    ) -> None:
        with transaction(self._conn):
            self._conn.execute(
                f"INSERT INTO watchlist ({_COLS}) VALUES (?,?,?,?,?) "
                'ON CONFLICT(slug, "rank") DO UPDATE SET '
                "pin_weight=excluded.pin_weight, alert_override=excluded.alert_override",
                (slug, rank, to_utc_iso(added_at), pin_weight, int(alert_override)),
            )

    def remove(self, slug: str, rank: int) -> bool:
        with transaction(self._conn):
            cur = self._conn.execute(
                'DELETE FROM watchlist WHERE slug=? AND "rank"=?', (slug, rank)
            )
        return cur.rowcount > 0

    def all(self) -> list[WatchlistEntry]:
        rows = self._conn.execute(f'SELECT {_COLS} FROM watchlist ORDER BY slug, "rank"')
        return [_to_entry(r) for r in rows]

    def get(self, slug: str, rank: int) -> WatchlistEntry | None:
        row = self._conn.execute(
            f'SELECT {_COLS} FROM watchlist WHERE slug=? AND "rank"=?', (slug, rank)
        ).fetchone()
        return _to_entry(row) if row else None

    def set_pin(self, slug: str, rank: int, weight: float) -> None:
        with transaction(self._conn):
            self._conn.execute(
                'UPDATE watchlist SET pin_weight=? WHERE slug=? AND "rank"=?',
                (weight, slug, rank),
            )

    def set_alert_override(self, slug: str, rank: int, value: bool) -> None:
        with transaction(self._conn):
            self._conn.execute(
                'UPDATE watchlist SET alert_override=? WHERE slug=? AND "rank"=?',
                (int(value), slug, rank),
            )


def _to_entry(row: sqlite3.Row) -> WatchlistEntry:
    return WatchlistEntry(
        slug=row["slug"],
        rank=row["rank"],
        added_at=datetime.fromisoformat(row["added_at"]),
        pin_weight=row["pin_weight"],
        alert_override=bool(row["alert_override"]),
    )
