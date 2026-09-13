from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.gui.app import build_app
from wfm.services.context import AppContext

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _client(conn) -> TestClient:
    ctx = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    client = TestClient(build_app(ctx))
    client.ctx = ctx
    return client


def test_a_published_signal_reaches_a_connected_websocket_client(conn):
    client = _client(conn)
    with client.websocket_connect("/ws/signals") as websocket:
        client.ctx.broadcaster.publish({"slug": "x", "rank": 0, "analyzer": "flip"})
        received = websocket.receive_json()
    assert received == {"slug": "x", "rank": 0, "analyzer": "flip"}


def test_disconnecting_unsubscribes_the_client(conn):
    client = _client(conn)
    with client.websocket_connect("/ws/signals"):
        assert client.ctx.broadcaster.subscriber_count == 1
    assert client.ctx.broadcaster.subscriber_count == 0


def test_a_client_message_does_not_end_the_feed(conn):
    """The feed reads the socket only to notice a disconnect.

    A client that says anything at all -- a keepalive, a stray frame -- must not be
    mistaken for one and dropped off the feed.
    """
    client = _client(conn)
    with client.websocket_connect("/ws/signals") as websocket:
        websocket.send_text("ping")
        client.ctx.broadcaster.publish({"slug": "x", "rank": 0, "analyzer": "flip"})
        received = websocket.receive_json()
    assert received == {"slug": "x", "rank": 0, "analyzer": "flip"}
