from __future__ import annotations

import sqlite3

DDL = """
ALTER TABLE order_snapshots ADD COLUMN online_bid_depth_1 INTEGER;
ALTER TABLE order_snapshots ADD COLUMN online_bid_depth_2 INTEGER;
ALTER TABLE order_snapshots ADD COLUMN online_bid_depth_3 INTEGER;
ALTER TABLE order_snapshots ADD COLUMN online_bid_depth_4 INTEGER;
ALTER TABLE order_snapshots ADD COLUMN online_bid_depth_5 INTEGER;
ALTER TABLE order_snapshots ADD COLUMN online_ask_depth_1 INTEGER;
ALTER TABLE order_snapshots ADD COLUMN online_ask_depth_2 INTEGER;
ALTER TABLE order_snapshots ADD COLUMN online_ask_depth_3 INTEGER;
ALTER TABLE order_snapshots ADD COLUMN online_ask_depth_4 INTEGER;
ALTER TABLE order_snapshots ADD COLUMN online_ask_depth_5 INTEGER;
"""


def up(conn: sqlite3.Connection) -> None:
    # Statements executed individually inside the caller's transaction, matching
    # m0001: executescript() would issue an implicit COMMIT and break atomicity
    # against the user_version bump in migrate().
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
