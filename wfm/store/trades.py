from __future__ import annotations

import sqlite3
from datetime import datetime

from wfm.models import Direction, Trade
from wfm.store.db import to_utc_iso, transaction

_COLS = 'id, slug, "rank", ts, side, quantity, platinum, note'


class TradesRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record(self, trade: Trade) -> int:
        with transaction(self._conn):
            cur = self._conn.execute(
                'INSERT INTO trades (slug, "rank", ts, side, quantity, platinum, note) '
                "VALUES (?,?,?,?,?,?,?)",
                (
                    trade.slug, trade.rank, to_utc_iso(trade.ts), trade.side.value,
                    trade.quantity, trade.platinum, trade.note,
                ),
            )
        return int(cur.lastrowid)

    def all_for(self, slug: str, rank: int) -> list[Trade]:
        rows = self._conn.execute(
            f'SELECT {_COLS} FROM trades WHERE slug=? AND "rank"=? ORDER BY ts, id', (slug, rank)
        )
        return [_to_trade(r) for r in rows]

    def all(self) -> list[Trade]:
        rows = self._conn.execute(f"SELECT {_COLS} FROM trades ORDER BY ts, id")
        return [_to_trade(r) for r in rows]

    def holdings(self) -> list[tuple[str, int, int, float]]:
        rows = self._conn.execute(
            'SELECT slug, "rank", quantity, avg_cost FROM holdings ORDER BY slug, "rank"'
        )
        return [(r["slug"], int(r["rank"]), int(r["quantity"]), r["avg_cost"]) for r in rows]


def _to_trade(row: sqlite3.Row) -> Trade:
    return Trade(
        id=row["id"], slug=row["slug"], rank=row["rank"],
        ts=datetime.fromisoformat(row["ts"]), side=Direction(row["side"]),
        quantity=row["quantity"], platinum=row["platinum"], note=row["note"],
    )
