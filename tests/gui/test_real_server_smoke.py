"""Verifies the GUI actually works in production, not just under TestClient.

TestClient runs the app through its own thread-portal (see tests/gui/conftest.py's
`conn` fixture docstring), which requires `check_same_thread=False` regardless of
whether routes are sync or async -- so it cannot tell us whether a real deployment
(one `uvicorn.Server` on the event-loop thread that created the connection) works.

This test binds a real `uvicorn.Server` to an ephemeral port and drives it with a
real HTTP client, using a connection built exactly the way production does
(`wfm.store.db.connect()`, i.e. `check_same_thread=True`, no override). It is the
test that would have caught the original bug: 14 of 15 routes were `def` (dispatched
to an anyio worker thread, different from the thread that created the sqlite3
connection), so they raised `sqlite3.ProgrammingError` under a real server even
though the same routes returned 200 under TestClient's own thread model.
"""

from __future__ import annotations

import asyncio
import socket
from datetime import datetime, timezone

import httpx
import uvicorn

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.gui.app import build_app
from wfm.models import Item
from wfm.services.context import AppContext
from wfm.store.db import connect
from wfm.store.migrate import migrate

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_every_router_answers_over_a_real_server_not_500(tmp_path):
    # Production connection settings: check_same_thread=True (the default), not the
    # tests/gui/conftest.py override. This is the whole point of the test.
    conn = connect(tmp_path / "real.db")
    migrate(conn)
    ctx = AppContext(Config(pid_file=tmp_path / "wfm.pid"), conn=conn, clock=FakeClock(start_utc=NOW))
    ctx.items.upsert_many([Item(slug="x", name="X", url_name="x")])

    app = build_app(ctx)
    port = _free_port()
    # Mirrors wfm.services.daemon_service.start's own uvicorn.Config/Server pattern.
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started or server_task.done():
                break
            await asyncio.sleep(0.01)
        assert server.started, "server failed to start"

        base_url = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base_url) as client:
            checks = [
                ("GET", "/watchlist"),
                ("GET", "/items/x"),
                ("GET", "/groups"),
                ("GET", "/daemon/status"),
                ("GET", "/ledger/holdings"),
            ]
            for method, path in checks:
                response = await client.request(method, path)
                assert response.status_code != 500, (
                    f"{method} {path} returned 500: {response.text}"
                )
                assert response.status_code < 500
    finally:
        server.should_exit = True
        await server_task
        conn.close()
