import sqlite3

import pytest

from wfm.store.migrate import migrate


@pytest.fixture
def conn(tmp_path):
    """check_same_thread=False here is a TestClient-harness requirement, not a
    workaround for the sqlite3-thread-affinity production bug fixed at the route
    level (every wfm.gui route is now `async def`, so in production the connection
    is only ever touched from the event-loop thread that created it).

    `TestClient` runs the app through its own thread-portal (a worker thread hosts
    the event loop for each request), so even an all-`async def` app touches the
    connection from a different OS thread than the fixture created it on. Verified
    directly: with the plain `check_same_thread=True` root `conn` fixture, every
    route in tests/gui/ -- including items.py's `get_item`, already `async def`
    before this fix -- raised `sqlite3.ProgrammingError: SQLite objects created in
    a thread can only be used in that same thread`. The real-server smoke test in
    tests/gui/test_real_server_smoke.py runs the production connection settings
    against a real uvicorn.Server and is what actually proves the route-level fix.
    """
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
