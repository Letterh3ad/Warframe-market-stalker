import sqlite3

import pytest

from wfm.store.migrate import migrate


@pytest.fixture
def conn(tmp_path):
    """Connection fixture with check_same_thread=False for TestClient compatibility."""
    path = tmp_path / "test.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None, timeout=30.0, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA synchronous=NORMAL")
    migrate(connection)
    yield connection
    connection.close()
