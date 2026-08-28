from __future__ import annotations

import sqlite3
from datetime import datetime

from wfm.models import SweepStatus
from wfm.store.db import to_utc_iso, transaction


class SweepStateRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def start(self, sweep: str, when: datetime) -> None:
        # cursor is deliberately left untouched on conflict: a sweep that halted
        # yesterday resumes where it stopped instead of restarting from item one.
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO sweep_state (sweep, cursor, started_at, updated_at, status) "
                "VALUES (?, NULL, ?, ?, ?) "
                "ON CONFLICT(sweep) DO UPDATE SET "
                "started_at=excluded.started_at, updated_at=excluded.updated_at, "
                "status=excluded.status, reason=NULL",
                (sweep, to_utc_iso(when), to_utc_iso(when), SweepStatus.RUNNING.value),
            )

    def checkpoint(self, sweep: str, cursor: str, when: datetime, done_count: int) -> None:
        with transaction(self._conn):
            cur = self._conn.execute(
                "UPDATE sweep_state SET cursor=?, updated_at=?, done_count=? WHERE sweep=?",
                (cursor, to_utc_iso(when), done_count, sweep),
            )
        _require_hit(cur.rowcount, sweep)

    def finish(self, sweep: str, when: datetime) -> None:
        _require_hit(self._set_status(sweep, SweepStatus.DONE, when, reason=None), sweep)

    def halt(self, sweep: str, reason: str, when: datetime) -> None:
        # Upsert rather than raise: halt is the circuit breaker's record of why the
        # next run must not charge back in, so it has to land even when start() is
        # what failed and no row exists yet.
        if self._set_status(sweep, SweepStatus.HALTED, when, reason=reason):
            return
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO sweep_state (sweep, cursor, started_at, updated_at, status, reason) "
                "VALUES (?, NULL, ?, ?, ?, ?)",
                (sweep, to_utc_iso(when), to_utc_iso(when), SweepStatus.HALTED.value, reason),
            )

    def get(self, sweep: str) -> dict | None:
        row = self._conn.execute(
            "SELECT sweep, cursor, started_at, updated_at, status, reason, done_count "
            "FROM sweep_state WHERE sweep=?",
            (sweep,),
        ).fetchone()
        return dict(row) if row else None

    def _set_status(
        self, sweep: str, status: SweepStatus, when: datetime, reason: str | None
    ) -> int:
        with transaction(self._conn):
            cur = self._conn.execute(
                "UPDATE sweep_state SET status=?, reason=?, updated_at=? WHERE sweep=?",
                (status.value, reason, to_utc_iso(when), sweep),
            )
        return cur.rowcount


def _require_hit(rowcount: int, sweep: str) -> None:
    """A typo in a sweep name must not look like a sweep that never advances."""
    if rowcount == 0:
        raise KeyError(f"no such sweep: {sweep}")
