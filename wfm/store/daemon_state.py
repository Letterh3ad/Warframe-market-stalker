from __future__ import annotations

import sqlite3
from datetime import date, datetime

from wfm.store.db import to_utc_iso, transaction

_DAILY_COLUMNS = {"sweep": "last_sweep_date", "digest": "last_digest_date"}

_COLS = "pid, started_at, heartbeat_at, status, detail, last_sweep_date, last_digest_date"


class DaemonStateRepo:
    """Single-row daemon identity, heartbeat and daily-task ledger.

    Legal `status` values: 'running', 'stopping', 'stopped', 'halted'. Not enforced
    with a CHECK constraint so a future status does not require a migration.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def mark_started(self, pid: int, when: datetime) -> None:
        # Sets status='running' unconditionally, clearing any stale 'stopping' flag
        # left by a crashed daemon so it cannot halt the next one before it starts.
        # last_sweep_date/last_digest_date are deliberately untouched: a restart
        # clears the daemon's identity, not the fact that today's work already ran.
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO daemon_state (id, pid, started_at, heartbeat_at, status, detail) "
                "VALUES (1, ?, ?, ?, 'running', NULL) "
                "ON CONFLICT(id) DO UPDATE SET pid=excluded.pid, started_at=excluded.started_at, "
                "heartbeat_at=excluded.heartbeat_at, status='running', detail=NULL",
                (pid, to_utc_iso(when), to_utc_iso(when)),
            )

    def heartbeat(self, when: datetime, status: str = "running", detail: str | None = None) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE daemon_state SET heartbeat_at=?, status=?, detail=? WHERE id=1",
                (to_utc_iso(when), status, detail),
            )

    def mark_stopped(self, when: datetime, detail: str | None = None) -> None:
        self.heartbeat(when, status="stopped", detail=detail)

    def request_stop(self, when: datetime) -> bool:
        """Ask a running daemon to exit cleanly. False when nothing is running."""
        with transaction(self._conn):
            cur = self._conn.execute(
                "UPDATE daemon_state SET status='stopping', heartbeat_at=? "
                "WHERE id=1 AND status != 'stopped'",
                (to_utc_iso(when),),
            )
        return cur.rowcount > 0

    def stop_requested(self) -> bool:
        """True while status is 'stopping'. The loop checks this once per iteration."""
        row = self._conn.execute("SELECT status FROM daemon_state WHERE id=1").fetchone()
        return row is not None and row["status"] == "stopping"

    def mark_daily_done(self, kind: str, day: date, when: datetime) -> None:
        """kind is 'sweep' or 'digest'. Raises ValueError on anything else."""
        column = _require_daily_column(kind)
        with transaction(self._conn):
            self._conn.execute(
                f"UPDATE daemon_state SET {column}=?, heartbeat_at=? WHERE id=1",
                (day.isoformat(), to_utc_iso(when)),
            )

    def daily_done(self, kind: str) -> date | None:
        column = _require_daily_column(kind)
        row = self._conn.execute(f"SELECT {column} FROM daemon_state WHERE id=1").fetchone()
        value = row[column] if row else None
        return date.fromisoformat(value) if value else None

    def get(self) -> dict | None:
        row = self._conn.execute(f"SELECT {_COLS} FROM daemon_state WHERE id=1").fetchone()
        return _to_state(row) if row else None


def _require_daily_column(kind: str) -> str:
    try:
        return _DAILY_COLUMNS[kind]
    except KeyError:
        raise ValueError(f"kind must be 'sweep' or 'digest', got {kind!r}") from None


def _to_state(row: sqlite3.Row) -> dict:
    return {
        "pid": row["pid"],
        "started_at": _parse_datetime(row["started_at"]),
        "heartbeat_at": _parse_datetime(row["heartbeat_at"]),
        "status": row["status"],
        "detail": row["detail"],
        "last_sweep_date": _parse_date(row["last_sweep_date"]),
        "last_digest_date": _parse_date(row["last_digest_date"]),
    }


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None
