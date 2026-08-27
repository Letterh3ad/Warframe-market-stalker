from __future__ import annotations

import sqlite3
from datetime import datetime

from wfm.models import BookSnapshot
from wfm.store.db import to_utc_iso, transaction

_DEPTH_LEVELS = 5
_COLS = (
    'slug, "rank", ts, best_bid, best_ask, online_best_bid, online_best_ask, '
    "bid_depth_1, bid_depth_2, bid_depth_3, bid_depth_4, bid_depth_5, "
    "ask_depth_1, ask_depth_2, ask_depth_3, ask_depth_4, ask_depth_5, "
    "bid_count, ask_count, online_bid_count, online_ask_count, stale_share"
)


def _pad(values: tuple[int, ...]) -> list[int | None]:
    padded = list(values[:_DEPTH_LEVELS])
    return padded + [None] * (_DEPTH_LEVELS - len(padded))


class OrderSnapshotsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(self, snapshot: BookSnapshot) -> None:
        row = [
            snapshot.slug, snapshot.rank, to_utc_iso(snapshot.ts),
            snapshot.best_bid, snapshot.best_ask,
            snapshot.online_best_bid, snapshot.online_best_ask,
            *_pad(snapshot.bid_depth), *_pad(snapshot.ask_depth),
            snapshot.bid_count, snapshot.ask_count,
            snapshot.online_bid_count, snapshot.online_ask_count, snapshot.stale_share,
        ]
        placeholders = ",".join("?" * len(row))
        with transaction(self._conn):
            self._conn.execute(
                f"INSERT OR REPLACE INTO order_snapshots ({_COLS}) VALUES ({placeholders})", row
            )

    def latest(self, slug: str, rank: int) -> BookSnapshot | None:
        row = self._conn.execute(
            f"SELECT {_COLS} FROM order_snapshots "
            'WHERE slug=? AND "rank"=? ORDER BY ts DESC LIMIT 1',
            (slug, rank),
        ).fetchone()
        return _to_snapshot(row) if row else None

    def recent(self, slug: str, rank: int, limit: int) -> list[BookSnapshot]:
        rows = self._conn.execute(
            f"SELECT {_COLS} FROM order_snapshots "
            'WHERE slug=? AND "rank"=? ORDER BY ts DESC LIMIT ?',
            (slug, rank, limit),
        )
        return [_to_snapshot(r) for r in rows]


class RawSnapshotsRepo:
    """Sampled raw payloads for debugging. Never read by the production path."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._seen = 0

    def maybe_store(
        self, slug: str, rank: int, ts: datetime, payload: str, sample_rate: int
    ) -> bool:
        if sample_rate <= 0:
            return False
        stored = self._seen % sample_rate == 0
        self._seen += 1
        if not stored:
            return False
        with transaction(self._conn):
            self._conn.execute(
                'INSERT OR REPLACE INTO order_snapshots_raw (slug, "rank", ts, payload) '
                "VALUES (?,?,?,?)",
                (slug, rank, to_utc_iso(ts), payload),
            )
        return True

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM order_snapshots_raw").fetchone()[0])


def _to_snapshot(row: sqlite3.Row) -> BookSnapshot:
    bid = tuple(v for v in (row[f"bid_depth_{i}"] for i in range(1, 6)) if v is not None)
    ask = tuple(v for v in (row[f"ask_depth_{i}"] for i in range(1, 6)) if v is not None)
    return BookSnapshot(
        slug=row["slug"], rank=row["rank"], ts=datetime.fromisoformat(row["ts"]),
        best_bid=row["best_bid"], best_ask=row["best_ask"],
        online_best_bid=row["online_best_bid"], online_best_ask=row["online_best_ask"],
        bid_depth=bid, ask_depth=ask,
        bid_count=row["bid_count"], ask_count=row["ask_count"],
        online_bid_count=row["online_bid_count"], online_ask_count=row["online_ask_count"],
        stale_share=row["stale_share"],
    )
