from wfm.store.db import connect
from wfm.store.migrate import SCHEMA_VERSION, current_version, migrate


def test_fresh_database_migrates_to_head(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert current_version(conn) == 0
    assert migrate(conn) == SCHEMA_VERSION
    assert current_version(conn) == SCHEMA_VERSION


def test_migrate_is_idempotent(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    before = _tables(conn)
    assert migrate(conn) == SCHEMA_VERSION
    assert _tables(conn) == before


def test_wal_and_foreign_keys_are_on(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_rows_are_mappings(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    assert conn.execute("SELECT 1 AS n").fetchone()["n"] == 1


def _tables(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
    return {r[0] for r in rows}
