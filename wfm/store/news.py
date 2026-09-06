from __future__ import annotations

import sqlite3
from datetime import datetime

from wfm.news.types import Article, ArticleStatus, NewsSource
from wfm.store.db import to_utc_iso, transaction


class NewsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_article(self, article: Article, fetched_at: datetime) -> int | None:
        """Returns the row id when the article is new or its content changed, and None
        when an identical article is already stored.

        None is the "nothing to do" signal the ingest loop uses to skip re-classifying
        an article it has already seen, which is what makes a poll that finds nothing
        new nearly free.
        """
        # article.content_hash is a stored field stamped by the source via .hashed()
        # while the body was still in hand. Never recompute it here: a DB-loaded article
        # has body="" and would hash to something that never matches.
        digest = article.content_hash
        existing = self._conn.execute(
            "SELECT id, content_hash FROM news_articles WHERE external_id=?",
            (article.external_id,),
        ).fetchone()

        if existing is not None and existing["content_hash"] == digest:
            return None

        published = to_utc_iso(article.published_at) if article.published_at else None
        with transaction(self._conn):
            if existing is None:
                cur = self._conn.execute(
                    "INSERT INTO news_articles (source, external_id, url, title, "
                    "published_at, fetched_at, content_hash, excerpt, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        article.source.value,
                        article.external_id,
                        article.url,
                        article.title,
                        published,
                        to_utc_iso(fetched_at),
                        digest,
                        article.excerpt,
                        ArticleStatus.PENDING.value,
                    ),
                )
                return int(cur.lastrowid)

            article_id = int(existing["id"])
            # The stored extraction was derived from text that no longer exists, so it
            # goes with the old content. The FK cascade takes the links with it.
            self._conn.execute(
                "DELETE FROM news_events WHERE article_id=?", (article_id,)
            )
            self._conn.execute(
                "UPDATE news_articles SET url=?, title=?, published_at=?, fetched_at=?, "
                "content_hash=?, excerpt=?, status=?, classifier_name=NULL, "
                "classifier_version=NULL, classified_at=NULL WHERE id=?",
                (
                    article.url,
                    article.title,
                    published,
                    to_utc_iso(fetched_at),
                    digest,
                    article.excerpt,
                    ArticleStatus.PENDING.value,
                    article_id,
                ),
            )
            return article_id

    def mark(
        self,
        article_id: int,
        status: ArticleStatus,
        classifier_name: str | None = None,
        classifier_version: str | None = None,
        when: datetime | None = None,
    ) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE news_articles SET status=?, classifier_name=?, "
                "classifier_version=?, classified_at=? WHERE id=?",
                (
                    status.value,
                    classifier_name,
                    classifier_version,
                    to_utc_iso(when) if when else None,
                    article_id,
                ),
            )


def _to_article(row: sqlite3.Row) -> Article:
    return Article(
        id=row["id"],
        source=NewsSource(row["source"]),
        external_id=row["external_id"],
        url=row["url"],
        title=row["title"],
        published_at=(
            datetime.fromisoformat(row["published_at"]) if row["published_at"] else None
        ),
        excerpt=row["excerpt"],
        status=ArticleStatus(row["status"]),
        content_hash=row["content_hash"],
    )
