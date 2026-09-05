import pytest

from wfm.store.db import connect
from wfm.store.migrations import m0001_initial as m0001
from wfm.store.migrations import m0002_online_depth, m0003_daemon_state
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


def test_a_failing_migration_leaves_no_partial_schema(tmp_path, monkeypatch):
    class _BrokenMigration:
        @staticmethod
        def up(conn):
            conn.execute("CREATE TABLE partial (id INTEGER PRIMARY KEY)")
            raise RuntimeError("boom")

    monkeypatch.setattr("wfm.store.migrate.MIGRATIONS", [_BrokenMigration])
    conn = connect(tmp_path / "t.db")

    with pytest.raises(RuntimeError, match="boom"):
        migrate(conn)

    assert current_version(conn) == 0
    assert "partial" not in _tables(conn)


def _tables(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
    return {r[0] for r in rows}


def test_a_database_from_a_newer_build_is_refused(conn):
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError):
        migrate(conn)


def test_ddl_ending_mid_statement_is_rejected(conn, monkeypatch):
    monkeypatch.setattr(m0001, "DDL", "CREATE TABLE ok(a);\nCREATE TABLE broken(a)\n")
    with pytest.raises(ValueError):
        m0001.up(conn)


def test_migrating_from_an_earlier_version_does_not_rerun_applied_steps(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    monkeypatch.setattr("wfm.store.migrate.MIGRATIONS", [m0001, m0002_online_depth])
    migrate(conn)
    assert current_version(conn) == 2

    def _boom(_conn):
        raise AssertionError("must not re-run an already-applied migration")

    monkeypatch.setattr(m0001, "up", _boom)
    monkeypatch.setattr(m0002_online_depth, "up", _boom)
    monkeypatch.setattr(
        "wfm.store.migrate.MIGRATIONS", [m0001, m0002_online_depth, m0003_daemon_state]
    )

    migrate(conn)
    assert current_version(conn) == 3
    assert "daemon_state" in _tables(conn)
