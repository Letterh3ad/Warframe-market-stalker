from __future__ import annotations

import sqlite3
from datetime import datetime

from wfm.store.db import to_utc_iso, transaction

_COLS = '"rank", last_polled_at, due_at, interval_minutes, unchanged_polls'


class PollStateRepo:
    """Persisted poll schedule, so a crash or a host sleep does not make a restart
    treat the entire watchlist as due at once.

    Timestamps are wall clock, not monotonic: stored as UTC ISO strings and returned
    as tz-aware datetimes. The in-memory poll queue runs on clock.now() monotonic
    seconds, which reset to zero on every process start and are meaningless across a
    restart; converting between the two schemes is the consumer's job, not this
    repo's.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(
        self,
        slug: str,
        rank: int,
        due_at: datetime,
        interval_minutes: float,
        unchanged_polls: int,
        last_polled_at: datetime | None = None,
    ) -> None:
        """Insert or replace one target's schedule."""
        with transaction(self._conn):
            self._conn.execute(
                'INSERT INTO poll_state (slug, "rank", last_polled_at, due_at, '
                "interval_minutes, unchanged_polls) VALUES (?,?,?,?,?,?) "
                'ON CONFLICT(slug, "rank") DO UPDATE SET '
                "last_polled_at=excluded.last_polled_at, due_at=excluded.due_at, "
                "interval_minutes=excluded.interval_minutes, "
                "unchanged_polls=excluded.unchanged_polls",
                (
                    slug,
                    rank,
                    to_utc_iso(last_polled_at) if last_polled_at is not None else None,
                    to_utc_iso(due_at),
                    interval_minutes,
                    unchanged_polls,
                ),
            )

    def all(self) -> dict[tuple[str, int], dict]:
        """Every stored schedule, keyed on (slug, rank)."""
        rows = self._conn.execute(f"SELECT slug, {_COLS} FROM poll_state")
        return {(row["slug"], row["rank"]): _to_state(row) for row in rows}

    def get(self, slug: str, rank: int) -> dict | None:
        row = self._conn.execute(
            f'SELECT slug, {_COLS} FROM poll_state WHERE slug=? AND "rank"=?', (slug, rank)
        ).fetchone()
        return _to_state(row) if row else None

    def delete(self, slug: str, rank: int) -> bool:
        """Drop a target that left the watchlist. False when there was no row."""
        with transaction(self._conn):
            cur = self._conn.execute(
                'DELETE FROM poll_state WHERE slug=? AND "rank"=?', (slug, rank)
            )
        return cur.rowcount > 0


def _to_state(row: sqlite3.Row) -> dict:
    return {
        "last_polled_at": _parse(row["last_polled_at"]),
        "due_at": _parse(row["due_at"]),
        "interval_minutes": row["interval_minutes"],
        "unchanged_polls": row["unchanged_polls"],
    }


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
