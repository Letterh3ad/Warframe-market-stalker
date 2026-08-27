from __future__ import annotations

import sqlite3
from datetime import datetime

from wfm.store.db import to_utc_iso, transaction


class HttpCacheRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, url: str) -> tuple[str | None, str | None, str] | None:
        row = self._conn.execute(
            "SELECT etag, last_modified, body FROM http_cache WHERE url=?", (url,)
        ).fetchone()
        if row is None:
            return None
        return (row["etag"], row["last_modified"], row["body"])

    def put(
        self, url: str, etag: str | None, last_modified: str | None, body: str, when: datetime
    ) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "INSERT OR REPLACE INTO http_cache (url, etag, last_modified, fetched_at, body) "
                "VALUES (?,?,?,?,?)",
                (url, etag, last_modified, to_utc_iso(when), body),
            )
