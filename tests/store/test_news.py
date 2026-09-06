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


from datetime import datetime, timezone

from wfm.news.types import Article, ArticleStatus, NewsSource
from wfm.store.news import NewsRepo

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _article(body="Body", external_id="a1"):
    # .hashed() is what a real Source calls at fetch time (Task 2); without it
    # content_hash stays "" for every body and upsert_article can never tell
    # articles apart.
    return Article(
        source=NewsSource.WARFRAME_NEWS,
        external_id=external_id,
        url="https://example.test/a1",
        title="Prime Vault: Mesa Prime Returns",
        body=body,
        published_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        excerpt="Mesa Prime returns",
    ).hashed()


def test_upsert_inserts_and_returns_an_id(conn):
    repo = NewsRepo(conn)
    article_id = repo.upsert_article(_article(), NOW)
    assert isinstance(article_id, int)
    row = conn.execute("SELECT * FROM news_articles WHERE id=?", (article_id,)).fetchone()
    assert row["status"] == "pending"
    assert row["source"] == "warframe_news"
    assert row["excerpt"] == "Mesa Prime returns"


def test_upsert_returns_none_when_content_is_unchanged(conn):
    repo = NewsRepo(conn)
    repo.upsert_article(_article(), NOW)
    assert repo.upsert_article(_article(), NOW) is None


def test_upsert_of_changed_content_resets_status_and_drops_stale_events(conn):
    repo = NewsRepo(conn)
    article_id = repo.upsert_article(_article(), NOW)
    repo.mark(article_id, ArticleStatus.CLASSIFIED, "fake", "1", NOW)
    conn.execute(
        "INSERT INTO news_events (article_id, event_type, subject_raw, direction, "
        "strength, confidence) VALUES (?,'vault_in','Mesa Prime','up',0.5,0.5)",
        (article_id,),
    )

    again = repo.upsert_article(_article(body="Body, edited"), NOW)

    assert again == article_id
    row = conn.execute("SELECT * FROM news_articles WHERE id=?", (article_id,)).fetchone()
    assert row["status"] == "pending"
    assert row["classifier_name"] is None
    assert row["classified_at"] is None
    assert conn.execute("SELECT COUNT(*) FROM news_events").fetchone()[0] == 0


def test_mark_records_the_classifier(conn):
    repo = NewsRepo(conn)
    article_id = repo.upsert_article(_article(), NOW)
    repo.mark(article_id, ArticleStatus.CLASSIFIED, "ollama", "qwen3:4b", NOW)
    row = conn.execute("SELECT * FROM news_articles WHERE id=?", (article_id,)).fetchone()
    assert row["status"] == "classified"
    assert row["classifier_name"] == "ollama"
    assert row["classifier_version"] == "qwen3:4b"
    assert row["classified_at"] == NOW.isoformat()
