from wfm.store.migrate import SCHEMA_VERSION, current_version


def _tables(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {r[0] for r in rows}


def test_m0004_creates_the_four_news_tables(conn):
    assert {
        "news_articles",
        "news_candidates",
        "news_events",
        "news_item_links",
    } <= _tables(conn)


def test_schema_version_is_four(conn):
    assert SCHEMA_VERSION == 4
    assert current_version(conn) == 4


def test_deleting_an_article_cascades_to_events_and_links(conn):
    conn.execute(
        "INSERT INTO news_articles (id, source, external_id, url, title, "
        "fetched_at, content_hash, status) "
        "VALUES (1,'warframe_news','x','http://e','T','2026-09-06T00:00:00+00:00','h','pending')"
    )
    conn.execute(
        "INSERT INTO news_events (id, article_id, event_type, subject_raw, direction, "
        "strength, confidence) VALUES (1,1,'vault_in','Mesa Prime','up',0.5,0.5)"
    )
    conn.execute(
        'INSERT INTO news_item_links (event_id, slug, "rank", link_method, link_score, '
        "direction, weight) VALUES (1,'mesa_prime_set',0,'exact',1.0,'up',1.0)"
    )
    conn.execute(
        "INSERT INTO news_candidates (article_id, slug, name, score, context) "
        "VALUES (1,'mesa_prime_set','Mesa Prime Set',1.0,'ctx')"
    )
    conn.execute("DELETE FROM news_articles WHERE id=1")
    assert conn.execute("SELECT COUNT(*) FROM news_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM news_item_links").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM news_candidates").fetchone()[0] == 0


def test_external_id_is_unique(conn):
    import sqlite3

    import pytest

    row = (
        "INSERT INTO news_articles (source, external_id, url, title, fetched_at, "
        "content_hash, status) "
        "VALUES ('warframe_news','dup','http://e','T','2026-09-06T00:00:00+00:00','h','pending')"
    )
    conn.execute(row)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(row)
