from __future__ import annotations

import sqlite3

DDL = """
CREATE TABLE daemon_state (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    pid              INTEGER,
    started_at       TEXT,
    heartbeat_at     TEXT,
    status           TEXT NOT NULL,
    detail           TEXT,
    last_sweep_date  TEXT,
    last_digest_date TEXT
);

CREATE TABLE poll_state (
    slug             TEXT    NOT NULL,
    "rank"           INTEGER NOT NULL,
    last_polled_at   TEXT,
    due_at           TEXT    NOT NULL,
    interval_minutes REAL    NOT NULL,
    unchanged_polls  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (slug, "rank")
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    # Statements executed individually inside the caller's transaction, matching
    # m0001 and m0002: executescript() would issue an implicit COMMIT and break
    # atomicity against the user_version bump in migrate().
    buf = ""
    for line in DDL.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            statement = buf.strip()
            if statement:
                conn.execute(statement)
            buf = ""
    if buf.strip():
        raise ValueError(f"migration DDL ends mid-statement: {buf.strip()[:80]!r}")
