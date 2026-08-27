from __future__ import annotations

import sqlite3
from datetime import datetime

from wfm.models import Group
from wfm.store.db import to_utc_iso, transaction


class GroupsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(self, name: str, created_at: datetime) -> Group:
        if self.get(name) is not None:
            raise ValueError(f"group already exists: {name}")
        with transaction(self._conn):
            cur = self._conn.execute(
                "INSERT INTO groups (name, created_at) VALUES (?,?)",
                (name, to_utc_iso(created_at)),
            )
        return Group(name=name, created_at=created_at, id=int(cur.lastrowid))

    def delete(self, name: str) -> bool:
        with transaction(self._conn):
            cur = self._conn.execute("DELETE FROM groups WHERE name=?", (name,))
        return cur.rowcount > 0

    def get(self, name: str) -> Group | None:
        row = self._conn.execute(
            "SELECT id, name, created_at FROM groups WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            return None
        return Group(
            id=row["id"], name=row["name"], created_at=datetime.fromisoformat(row["created_at"])
        )

    def all(self) -> list[Group]:
        rows = self._conn.execute("SELECT id, name, created_at FROM groups ORDER BY name")
        return [
            Group(id=r["id"], name=r["name"], created_at=datetime.fromisoformat(r["created_at"]))
            for r in rows
        ]

    def add_member(self, name: str, slug: str, rank: int) -> None:
        group = self._require(name)
        with transaction(self._conn):
            self._conn.execute(
                'INSERT OR IGNORE INTO group_members (group_id, slug, "rank") VALUES (?,?,?)',
                (group.id, slug, rank),
            )

    def remove_member(self, name: str, slug: str, rank: int) -> bool:
        group = self._require(name)
        with transaction(self._conn):
            cur = self._conn.execute(
                'DELETE FROM group_members WHERE group_id=? AND slug=? AND "rank"=?',
                (group.id, slug, rank),
            )
        return cur.rowcount > 0

    def members(self, name: str) -> list[tuple[str, int]]:
        group = self._require(name)
        rows = self._conn.execute(
            'SELECT slug, "rank" FROM group_members WHERE group_id=? ORDER BY slug, "rank"',
            (group.id,),
        )
        return [(r["slug"], int(r["rank"])) for r in rows]

    def _require(self, name: str) -> Group:
        group = self.get(name)
        if group is None:
            raise KeyError(f"no such group: {name}")
        return group
