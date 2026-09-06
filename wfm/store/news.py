from __future__ import annotations

import sqlite3
from datetime import datetime

from wfm.news.types import (
    Article,
    ArticleStatus,
    Candidate,
    ExtractedEvent,
    ItemLink,
    LinkMethod,
    NewsDirection,
    NewsSource,
)
from wfm.store.db import to_utc_iso, transaction

_ARTICLE_COLS = (
    "id, source, external_id, url, title, published_at, fetched_at, content_hash, "
    "excerpt, status, classifier_name, classifier_version, classified_at"
)


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

    def insert_candidates(self, article_id: int, candidates: list[Candidate]) -> int:
        """The gate's output: which catalog items this article mentions, and the text
        around each. This is the classifier's input, stored so the pipeline can stay
        asynchronous without ever persisting an article body or re-fetching one.
        """
        unique: dict[str, Candidate] = {c.slug: c for c in candidates}
        if not unique:
            return 0
        with transaction(self._conn):
            self._conn.executemany(
                "INSERT INTO news_candidates (article_id, slug, name, score, context) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(article_id, slug) DO UPDATE SET "
                "name=excluded.name, score=excluded.score, context=excluded.context",
                [
                    (article_id, c.slug, c.name, c.score, c.context)
                    for c in unique.values()
                ],
            )
        return len(unique)

    def candidates_for(self, article_id: int) -> list[Candidate]:
        rows = self._conn.execute(
            "SELECT slug, name, score, context FROM news_candidates "
            "WHERE article_id=? ORDER BY score DESC, slug",
            (article_id,),
        )
        # start/end are match offsets into an article body that is not persisted, so a
        # candidate read back from the database reports 0 for both. Nothing downstream
        # of the gate uses them; the stored context is what the classifier sees.
        return [
            Candidate(
                slug=r["slug"],
                name=r["name"],
                score=r["score"],
                context=r["context"],
                start=0,
                end=0,
            )
            for r in rows
        ]

    def insert_events(self, article_id: int, events: list[ExtractedEvent]) -> list[int]:
        """Ids come back in the same order as `events`, so the caller can pair each
        returned id with the links it derived from that event.
        """
        ids: list[int] = []
        with transaction(self._conn):
            for event in events:
                cur = self._conn.execute(
                    "INSERT INTO news_events (article_id, event_type, subject_raw, "
                    "direction, strength, confidence, rationale, effective_at, raw_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        article_id,
                        event.event_type.value,
                        event.subject_raw,
                        event.direction.value,
                        event.strength,
                        event.confidence,
                        event.rationale,
                        to_utc_iso(event.effective_at) if event.effective_at else None,
                        event.raw_json,
                    ),
                )
                ids.append(int(cur.lastrowid))
        return ids

    def insert_links(self, event_id: int, links: list[ItemLink]) -> int:
        """Returns the number of distinct (slug, rank) links written.

        Input is deduplicated first, so the return value means "rows written" rather than
        "rows attempted". Counting the table afterwards would report every link on the
        event, which equals the write count only while the event is fresh.

        ON CONFLICT DO UPDATE rather than INSERT OR REPLACE: the latter deletes and
        reinserts, churning the surrogate primary key on every re-link.
        """
        unique: dict[tuple[str, int], ItemLink] = {}
        for link in links:
            unique[(link.slug, link.rank)] = link
        if not unique:
            return 0
        with transaction(self._conn):
            self._conn.executemany(
                'INSERT INTO news_item_links (event_id, slug, "rank", link_method, '
                "link_score, direction, weight) VALUES (?,?,?,?,?,?,?) "
                'ON CONFLICT(event_id, slug, "rank") DO UPDATE SET '
                "link_method=excluded.link_method, link_score=excluded.link_score, "
                "direction=excluded.direction, weight=excluded.weight",
                [
                    (
                        event_id,
                        link.slug,
                        link.rank,
                        link.link_method.value,
                        link.link_score,
                        link.direction.value,
                        link.weight,
                    )
                    for link in unique.values()
                ],
            )
        return len(unique)

    def replace_links_for(self, event_id: int, links: list[ItemLink]) -> int:
        """Re-link a stored event without re-invoking the classifier: used when the
        catalog grows or the fuzzy threshold changes.
        """
        with transaction(self._conn):
            self._conn.execute(
                "DELETE FROM news_item_links WHERE event_id=?", (event_id,)
            )
            return self.insert_links(event_id, links)

    def pending(self, limit: int = 50) -> list[Article]:
        rows = self._conn.execute(
            f"SELECT {_ARTICLE_COLS} FROM news_articles WHERE status=? "
            "ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC LIMIT ?",
            (ArticleStatus.PENDING.value, limit),
        )
        return [_to_article(r) for r in rows]

    def recent_articles(
        self, limit: int = 50, offset: int = 0, source: NewsSource | None = None
    ) -> list[Article]:
        where = "WHERE source=?" if source is not None else ""
        params: list = [source.value] if source is not None else []
        params += [limit, offset]
        rows = self._conn.execute(
            f"SELECT {_ARTICLE_COLS} FROM news_articles {where} "
            "ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC LIMIT ? OFFSET ?",
            params,
        )
        return [_to_article(r) for r in rows]

    def links_for_event(self, event_id: int) -> list[ItemLink]:
        rows = self._conn.execute(
            'SELECT event_id, slug, "rank", link_method, link_score, direction, weight '
            "FROM news_item_links WHERE event_id=? ORDER BY weight DESC, slug",
            (event_id,),
        )
        return [
            ItemLink(
                event_id=r["event_id"],
                slug=r["slug"],
                rank=r["rank"],
                link_method=LinkMethod(r["link_method"]),
                link_score=r["link_score"],
                direction=NewsDirection(r["direction"]),
                weight=r["weight"],
            )
            for r in rows
        ]


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
