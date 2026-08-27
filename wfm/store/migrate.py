from __future__ import annotations

import sqlite3

from wfm.store.db import transaction
from wfm.store.migrations import MIGRATIONS

SCHEMA_VERSION = len(MIGRATIONS)


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    version = current_version(conn)
    for index, module in enumerate(MIGRATIONS, start=1):
        if index <= version:
            continue
        with transaction(conn):
            module.up(conn)
            conn.execute(f"PRAGMA user_version={index}")
    return SCHEMA_VERSION
