from datetime import datetime, timezone

from wfm.store.http_cache import HttpCacheRepo

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def test_put_then_get(conn):
    repo = HttpCacheRepo(conn)
    assert repo.get("https://api/x") is None
    repo.put("https://api/x", etag='W/"abc"', last_modified=None, body='{"a":1}', when=NOW)
    assert repo.get("https://api/x") == ('W/"abc"', None, '{"a":1}')


def test_put_overwrites(conn):
    repo = HttpCacheRepo(conn)
    repo.put("https://api/x", etag="1", last_modified=None, body="a", when=NOW)
    repo.put("https://api/x", etag="2", last_modified=None, body="b", when=NOW)
    assert repo.get("https://api/x") == ("2", None, "b")
