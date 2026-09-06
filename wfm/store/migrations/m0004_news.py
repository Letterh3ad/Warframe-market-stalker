from __future__ import annotations

import sqlite3

DDL = """
CREATE TABLE news_articles (
    id                 INTEGER PRIMARY KEY,
    source             TEXT    NOT NULL,
    external_id        TEXT    NOT NULL UNIQUE,
    url                TEXT    NOT NULL,
    title              TEXT    NOT NULL,
    published_at       TEXT,
    fetched_at         TEXT    NOT NULL,
    content_hash       TEXT    NOT NULL,
    excerpt            TEXT,
    status             TEXT    NOT NULL DEFAULT 'pending',
    classifier_name    TEXT,
    classifier_version TEXT,
    classified_at      TEXT
);

CREATE INDEX idx_news_articles_status ON news_articles (status);

CREATE INDEX idx_news_articles_published ON news_articles (published_at DESC);

CREATE TABLE news_candidates (
    id         INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    slug       TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    score      REAL    NOT NULL,
    context    TEXT    NOT NULL,
    UNIQUE (article_id, slug)
);

CREATE INDEX idx_news_candidates_article ON news_candidates (article_id);

CREATE TABLE news_events (
    id           INTEGER PRIMARY KEY,
    article_id   INTEGER NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    event_type   TEXT    NOT NULL,
    subject_raw  TEXT    NOT NULL,
    direction    TEXT    NOT NULL,
    strength     REAL    NOT NULL,
    confidence   REAL    NOT NULL,
    rationale    TEXT,
    effective_at TEXT,
    raw_json     TEXT
);

CREATE INDEX idx_news_events_article ON news_events (article_id);

CREATE INDEX idx_news_events_effective ON news_events (effective_at);

CREATE TABLE news_item_links (
    id          INTEGER PRIMARY KEY,
    event_id    INTEGER NOT NULL REFERENCES news_events(id) ON DELETE CASCADE,
    slug        TEXT    NOT NULL,
    "rank"      INTEGER NOT NULL,
    link_method TEXT    NOT NULL,
    link_score  REAL    NOT NULL,
    direction   TEXT    NOT NULL,
    weight      REAL    NOT NULL,
    UNIQUE (event_id, slug, "rank")
);

CREATE INDEX idx_news_links_item ON news_item_links (slug, "rank");

CREATE INDEX idx_news_links_event ON news_item_links (event_id);
"""


def up(conn: sqlite3.Connection) -> None:
    # Statements executed individually inside the caller's transaction, matching
    # m0001 through m0003: executescript() would issue an implicit COMMIT and break
    # atomicity against the user_version bump in migrate().
    buf = ""
    for line in DDL.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            statement = buf.strip()
            if statement:
                conn.execute(statement)
            buf = ""
    if buf.strip():
        raise ValueError(f"migration DDL ends mid-statement: {buf.strip()[:80]!r}")
