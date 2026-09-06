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


from wfm.news.types import ExtractedEvent, EventType, ItemLink, LinkMethod, NewsDirection


def _event(subject="Mesa Prime"):
    return ExtractedEvent(
        event_type=EventType.VAULT_OUT,
        subject_raw=subject,
        direction=NewsDirection.DOWN,
        strength=0.8,
        confidence=0.9,
        rationale="Unvaulting floods supply",
        effective_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )


def _link(slug="mesa_prime_set", method=LinkMethod.EXACT, weight=1.0):
    return ItemLink(
        slug=slug,
        rank=0,
        link_method=method,
        link_score=1.0,
        direction=NewsDirection.DOWN,
        weight=weight,
    )


def test_insert_candidates_stores_the_gate_output(conn):
    from wfm.news.types import Candidate

    repo = NewsRepo(conn)
    article_id = repo.upsert_article(_article(), NOW)
    written = repo.insert_candidates(
        article_id,
        [
            Candidate("mesa_prime_set", "Mesa Prime Set", 1.0, "Mesa Prime enters...", 4, 14),
            Candidate("nekros_prime_set", "Nekros Prime Set", 0.9, "and Nekros Prime", 30, 42),
        ],
    )
    assert written == 2

    stored = repo.candidates_for(article_id)
    assert [c.slug for c in stored] == ["mesa_prime_set", "nekros_prime_set"]
    assert stored[0].context == "Mesa Prime enters..."
    assert stored[0].score == 1.0


def test_insert_candidates_is_idempotent_per_article_and_slug(conn):
    from wfm.news.types import Candidate

    repo = NewsRepo(conn)
    article_id = repo.upsert_article(_article(), NOW)
    repo.insert_candidates(article_id, [Candidate("a", "A", 0.9, "old ctx", 0, 1)])
    repo.insert_candidates(article_id, [Candidate("a", "A", 1.0, "new ctx", 0, 1)])

    (only,) = repo.candidates_for(article_id)
    assert only.context == "new ctx"
    assert only.score == 1.0


def test_insert_events_returns_ids_in_order(conn):
    repo = NewsRepo(conn)
    article_id = repo.upsert_article(_article(), NOW)
    ids = repo.insert_events(article_id, [_event("Mesa Prime"), _event("Akjagara Prime")])
    assert len(ids) == 2
    subjects = [
        r[0]
        for r in conn.execute(
            "SELECT subject_raw FROM news_events WHERE id IN (?,?) ORDER BY id", ids
        )
    ]
    assert subjects == ["Mesa Prime", "Akjagara Prime"]


def test_insert_events_roundtrips_effective_at(conn):
    repo = NewsRepo(conn)
    article_id = repo.upsert_article(_article(), NOW)
    (event_id,) = repo.insert_events(article_id, [_event()])
    row = conn.execute(
        "SELECT effective_at FROM news_events WHERE id=?", (event_id,)
    ).fetchone()
    assert row["effective_at"] == "2026-09-20T00:00:00+00:00"


def test_insert_links_writes_rows(conn):
    repo = NewsRepo(conn)
    article_id = repo.upsert_article(_article(), NOW)
    (event_id,) = repo.insert_events(article_id, [_event()])
    written = repo.insert_links(event_id, [_link(), _link("mesa_prime_blueprint")])
    assert written == 2
    assert conn.execute("SELECT COUNT(*) FROM news_item_links").fetchone()[0] == 2


def test_replace_links_swaps_the_whole_set_without_touching_the_event(conn):
    repo = NewsRepo(conn)
    article_id = repo.upsert_article(_article(), NOW)
    (event_id,) = repo.insert_events(article_id, [_event()])
    repo.insert_links(event_id, [_link("old_slug")])

    repo.replace_links_for(
        event_id, [_link("new_a", LinkMethod.SET_EXPANSION, 0.8), _link("new_b")]
    )

    slugs = {
        r[0] for r in conn.execute("SELECT slug FROM news_item_links WHERE event_id=?", (event_id,))
    }
    assert slugs == {"new_a", "new_b"}
    assert conn.execute("SELECT COUNT(*) FROM news_events").fetchone()[0] == 1


def test_insert_links_dedupes_and_reports_rows_written(conn):
    repo = NewsRepo(conn)
    article_id = repo.upsert_article(_article(), NOW)
    (event_id,) = repo.insert_events(article_id, [_event()])
    assert repo.insert_links(event_id, [_link(), _link()]) == 1
    assert conn.execute("SELECT COUNT(*) FROM news_item_links").fetchone()[0] == 1


def test_insert_links_counts_writes_not_total_links_on_the_event(conn):
    # Guards the bug this replaced: returning COUNT(*) for the event passes only while
    # the event is fresh, then silently over-reports on every later call.
    repo = NewsRepo(conn)
    article_id = repo.upsert_article(_article(), NOW)
    (event_id,) = repo.insert_events(article_id, [_event()])
    repo.insert_links(event_id, [_link("a"), _link("b")])
    assert repo.insert_links(event_id, [_link("c")]) == 1


def test_reinserting_a_link_updates_in_place_without_churning_its_id(conn):
    repo = NewsRepo(conn)
    article_id = repo.upsert_article(_article(), NOW)
    (event_id,) = repo.insert_events(article_id, [_event()])
    repo.insert_links(event_id, [_link("mesa_prime_set", LinkMethod.EXACT, 1.0)])
    before = conn.execute("SELECT id FROM news_item_links").fetchone()[0]

    repo.insert_links(event_id, [_link("mesa_prime_set", LinkMethod.FUZZY, 0.5)])

    row = conn.execute("SELECT id, link_method, weight FROM news_item_links").fetchone()
    assert row["id"] == before
    assert row["link_method"] == "fuzzy"
    assert row["weight"] == 0.5


def test_pending_returns_only_unclassified_newest_first(conn):
    repo = NewsRepo(conn)
    first = repo.upsert_article(_article(external_id="a1"), NOW)
    second = repo.upsert_article(_article(external_id="a2", body="Other"), NOW)
    repo.mark(first, ArticleStatus.CLASSIFIED, "fake", "1", NOW)

    pending = repo.pending()

    assert [a.id for a in pending] == [second]
    assert pending[0].external_id == "a2"


def test_pending_respects_limit(conn):
    repo = NewsRepo(conn)
    for n in range(5):
        repo.upsert_article(_article(external_id=f"a{n}", body=f"b{n}"), NOW)
    assert len(repo.pending(limit=2)) == 2


def test_recent_articles_filters_by_source(conn):
    repo = NewsRepo(conn)
    repo.upsert_article(_article(external_id="a1"), NOW)
    reddit = Article(
        source=NewsSource.REDDIT,
        external_id="r1",
        url="https://example.test/r1",
        title="Reddit post",
        body="text",
        published_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
    )
    repo.upsert_article(reddit, NOW)

    assert len(repo.recent_articles()) == 2
    only = repo.recent_articles(source=NewsSource.REDDIT)
    assert [a.external_id for a in only] == ["r1"]


def test_links_for_event_roundtrips(conn):
    repo = NewsRepo(conn)
    article_id = repo.upsert_article(_article(), NOW)
    (event_id,) = repo.insert_events(article_id, [_event()])
    repo.insert_links(event_id, [_link("mesa_prime_set", LinkMethod.EXACT, 1.0)])

    (link,) = repo.links_for_event(event_id)

    assert link.slug == "mesa_prime_set"
    assert link.link_method is LinkMethod.EXACT
    assert link.direction is NewsDirection.DOWN
    assert link.weight == 1.0
    assert link.event_id == event_id
