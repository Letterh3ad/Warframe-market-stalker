"""Ingest orchestration: fetch, gate, store. Nothing classifies anything yet.

The pipeline is asynchronous by design. This service writes articles and the gate's
candidates; a later plan's classifier reads the pending queue and the stored contexts.
Article bodies are held only for the length of one loop iteration and are never written.
"""

from __future__ import annotations

from dataclasses import replace

from wfm.news.fetch import NewsFetcher
from wfm.news.match import Lexicon, build_lexicon, find_candidates
from wfm.news.sources.base import Source
from wfm.news.sources.forums import ForumsSource
from wfm.news.sources.reddit import RedditSource
from wfm.news.sources.warframe_news import WarframeNewsSource
from wfm.news.types import Article, ArticleStatus, Candidate, NewsSource
from wfm.services.context import AppContext

EXCERPT_CANDIDATES = 3
EXCERPT_MAX_CHARS = 1000
EXCERPT_JOIN = " … "

SOURCE_BUILDERS = {
    "warframe_news": lambda ctx, fetcher: WarframeNewsSource(
        fetcher,
        known_ids=lambda: ctx.news.known_external_ids(NewsSource.WARFRAME_NEWS),
        max_bodies=ctx.config.news_max_bodies_per_poll,
    ),
    "forums": lambda ctx, fetcher: ForumsSource(fetcher),
    "reddit": lambda ctx, fetcher: RedditSource(fetcher),
}


def build_sources(ctx: AppContext, fetcher: NewsFetcher) -> list[Source]:
    """The configured sources, in configured order. Unknown names are ignored rather
    than fatal: a stale config entry should not stop the sources that do exist.
    `unknown_sources` surfaces the discard so it is not silent.
    """
    return [
        SOURCE_BUILDERS[name](ctx, fetcher)
        for name in ctx.config.news_sources
        if name in SOURCE_BUILDERS
    ]


def _resolved_sources(ctx: AppContext) -> list[str]:
    return [n for n in ctx.config.news_sources if n in SOURCE_BUILDERS]


def unknown_sources(ctx: AppContext) -> list[str]:
    """Configured names that resolve to no builder, in config order."""
    return [n for n in ctx.config.news_sources if n not in SOURCE_BUILDERS]


async def ingest(
    ctx: AppContext, sources: list[Source] | None = None, force: bool = False
) -> dict:
    if not ctx.config.news_enabled and not force:
        return {
            "enabled": False,
            "articles_stored": 0,
            "candidates_stored": 0,
            "unchanged": 0,
            "no_match": 0,
            "sources": {},
            "unknown_sources": [],
            "errors": {},
        }

    owned = sources is None
    fetcher = (
        NewsFetcher(
            clock=ctx.clock,
            user_agent=ctx.config.user_agent,
            min_interval_s=ctx.config.news_min_interval_s,
            timeout_s=ctx.config.request_timeout_s,
        )
        if owned
        else None
    )
    try:
        if sources is None:
            sources = build_sources(ctx, fetcher)
        # Built once per run, not per article: it is the whole catalog, and the catalog
        # cannot change mid-poll.
        lexicon = build_lexicon(ctx.items.all())
        summary = {
            "enabled": True,
            "articles_stored": 0,
            "candidates_stored": 0,
            "unchanged": 0,
            "no_match": 0,
            "sources": {},
            # Config names that resolved to no builder, so a typo does not hide silently.
            # Empty when sources were injected: nothing was resolved from config then.
            "unknown_sources": unknown_sources(ctx) if owned else [],
            "errors": {},
        }

        for source in sources:
            try:
                articles = await source.fetch()
            except Exception as exc:
                # Per source, so one dead upstream does not discard the others' work.
                summary["errors"][source.name.value] = str(exc)
                continue

            counts = {"fetched": len(articles), "stored": 0, "candidates": 0}
            for article in articles:
                stored, candidates = _absorb(ctx, article, lexicon)
                if not stored:
                    summary["unchanged"] += 1
                    continue
                counts["stored"] += 1
                counts["candidates"] += candidates
                summary["articles_stored"] += 1
                summary["candidates_stored"] += candidates
                if candidates == 0:
                    summary["no_match"] += 1
            summary["sources"][source.name.value] = counts

        return summary
    finally:
        if fetcher is not None:
            await fetcher.aclose()


def _absorb(ctx: AppContext, article: Article, lexicon: Lexicon) -> tuple[bool, int]:
    """Gate one article and persist the result. Returns (stored, candidate count)."""
    candidates = find_candidates(f"{article.title}\n\n{article.body}", lexicon)
    fetched_at = ctx.clock.utcnow()

    article_id = ctx.news.upsert_article(
        replace(article, excerpt=_excerpt(candidates)), fetched_at
    )
    if article_id is None:
        return False, 0

    # replace_candidates, not insert_candidates: on an edit the slugs the new text no
    # longer mentions must go, or the classifier is handed context from deleted text.
    written = ctx.news.replace_candidates(article_id, candidates)
    if written == 0:
        ctx.news.mark(article_id, ArticleStatus.NO_MATCH, when=fetched_at)
    return True, written


def _excerpt(candidates: list[Candidate]) -> str | None:
    """The matched sentences, which is what the GUI shows behind any later claim.

    None rather than "" when nothing matched: an absent excerpt and an empty one mean
    different things, and the column is nullable for that reason.
    """
    if not candidates:
        return None
    joined = EXCERPT_JOIN.join(c.context for c in candidates[:EXCERPT_CANDIDATES])
    return joined[:EXCERPT_MAX_CHARS]


def status(ctx: AppContext) -> dict:
    return {
        "enabled": ctx.config.news_enabled,
        "sources": _resolved_sources(ctx),
        "unknown_sources": unknown_sources(ctx),
        "articles": len(ctx.news.recent_articles(limit=10_000)),
        "pending": len(ctx.news.pending(limit=10_000)),
    }
