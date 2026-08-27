from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from wfm.models import Direction, Horizon, Signal
from wfm.store.db import to_utc_iso, transaction

_COLS = (
    'id, slug, "rank", analyzer, ts, horizon, direction, magnitude, confidence, '
    "evidence_json, expires_at, alerted_at"
)


class SignalsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(self, signal: Signal) -> int:
        with transaction(self._conn):
            cur = self._conn.execute(
                'INSERT INTO signals (slug, "rank", analyzer, ts, horizon, direction, '
                "magnitude, confidence, evidence_json, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    signal.slug, signal.rank, signal.analyzer, to_utc_iso(signal.ts),
                    signal.horizon.value, signal.direction.value, signal.magnitude,
                    signal.confidence, json.dumps(signal.evidence, sort_keys=True),
                    to_utc_iso(signal.expires_at) if signal.expires_at else None,
                ),
            )
        return int(cur.lastrowid)

    def open_for(self, slug: str, rank: int, analyzer: str, now: datetime) -> list[Signal]:
        rows = self._conn.execute(
            f"SELECT {_COLS} FROM signals "
            'WHERE slug=? AND "rank"=? AND analyzer=? '
            "AND (expires_at IS NULL OR expires_at > ?) ORDER BY ts DESC",
            (slug, rank, analyzer, to_utc_iso(now)),
        )
        return [_to_signal(r) for r in rows]

    def undelivered(self, horizon: Horizon) -> list[Signal]:
        rows = self._conn.execute(
            f"SELECT {_COLS} FROM signals WHERE horizon=? AND alerted_at IS NULL ORDER BY ts",
            (horizon.value,),
        )
        return [_to_signal(r) for r in rows]

    def mark_alerted(self, ids: list[int], when: datetime) -> int:
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        with transaction(self._conn):
            cur = self._conn.execute(
                f"UPDATE signals SET alerted_at=? "
                f"WHERE id IN ({placeholders}) AND alerted_at IS NULL",
                (to_utc_iso(when), *ids),
            )
        return cur.rowcount

    def query(
        self,
        since: datetime | None = None,
        analyzer: str | None = None,
        slug: str | None = None,
        limit: int = 100,
    ) -> list[Signal]:
        clauses: list[str] = []
        params: list = []
        if since is not None:
            clauses.append("ts >= ?")
            params.append(to_utc_iso(since))
        if analyzer is not None:
            clauses.append("analyzer = ?")
            params.append(analyzer)
        if slug is not None:
            clauses.append("slug = ?")
            params.append(slug)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._conn.execute(
            f"SELECT {_COLS} FROM signals {where} ORDER BY ts DESC LIMIT ?", params
        )
        return [_to_signal(r) for r in rows]

    def last_signal_at(self, slug: str, rank: int, analyzer: str) -> datetime | None:
        row = self._conn.execute(
            'SELECT MAX(ts) FROM signals WHERE slug=? AND "rank"=? AND analyzer=?',
            (slug, rank, analyzer),
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row[0] else None


def _to_signal(row: sqlite3.Row) -> Signal:
    return Signal(
        id=row["id"], slug=row["slug"], rank=row["rank"], analyzer=row["analyzer"],
        ts=datetime.fromisoformat(row["ts"]), horizon=Horizon(row["horizon"]),
        direction=Direction(row["direction"]), magnitude=row["magnitude"],
        confidence=row["confidence"], evidence=json.loads(row["evidence_json"]),
        expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        alerted_at=datetime.fromisoformat(row["alerted_at"]) if row["alerted_at"] else None,
    )
