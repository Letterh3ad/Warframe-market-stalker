"""Ingest orchestration: fetch, gate, store. Nothing classifies anything yet.

The pipeline is asynchronous by design. This service writes articles and the gate's
candidates; a later plan's classifier reads the pending queue and the stored contexts.
Article bodies are held only for the length of one loop iteration and are never written.
"""

from __future__ import annotations

from dataclasses import replace

from wfm.news.classify.base import ClassifierError, to_event
from wfm.news.classify.claude import ClaudeClassifier
from wfm.news.classify.ollama import OllamaClassifier
from wfm.news.fetch import NewsFetcher
from wfm.news.link import build_links
from wfm.news.match import Lexicon, build_lexicon, find_candidates
from wfm.news.sources.base import Source
from wfm.news.sources.forums import ForumsSource
from wfm.news.sources.reddit import RedditSource
from wfm.news.sources.warframe_news import WarframeNewsSource
from wfm.news.triage import triage
from wfm.news.types import (
    Article,
    ArticleStatus,
    Candidate,
    ClassifyRequest,
    LinkMethod,
    NewsSource,
)
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
    # Branch on the backend, not on truthiness: news_model is the Ollama tag and
    # stays set after a local benchmark, so a `or` chain reports an Ollama tag
    # under the claude backend.
    model = (
        ctx.config.news_claude_model
        if ctx.config.news_classifier == "claude"
        else ctx.config.news_model
    )
    return {
        "enabled": ctx.config.news_enabled,
        "sources": _resolved_sources(ctx),
        "unknown_sources": unknown_sources(ctx),
        "classifier": (
            "none"
            if ctx.config.news_classifier == "none"
            else f"{ctx.config.news_classifier}:{model}"
        ),
        "articles": ctx.news.count_articles(),
        "pending": ctx.news.count_articles(ArticleStatus.PENDING),
        "classified": ctx.news.count_articles(ArticleStatus.CLASSIFIED),
        "failed": ctx.news.count_articles(ArticleStatus.FAILED),
    }


KNOWN_CLASSIFIERS = ("none", "ollama", "claude")


def build_classifier(ctx: AppContext):
    """The configured backend, or None when classification is switched off.

    No "fake" branch: that would let a config typo silently write fabricated
    events into the ledger, indistinguishable from real classifier output. Tests
    inject FakeClassifier directly instead of going through this function.
    """
    name = ctx.config.news_classifier
    if name == "none":
        return None
    if name == "ollama":
        return OllamaClassifier(
            ctx.config.news_model, base_url=ctx.config.news_ollama_url
        )
    if name == "claude":
        return ClaudeClassifier(ctx.config.news_claude_model)
    raise ValueError(
        f"unknown news_classifier {name!r}; valid values are {KNOWN_CLASSIFIERS}"
    )


def _empty_summary(enabled: bool) -> dict:
    return {
        "enabled": enabled,
        "articles": 0,
        "events": 0,
        "links": 0,
        "failed": 0,
        "candidates_seen": 0,
        "candidates_skipped": 0,
        "errors": {},
    }


async def classify(ctx: AppContext, classifier=None, limit: int | None = None) -> dict:
    owned = classifier is None
    if owned:
        classifier = build_classifier(ctx)
    if classifier is None:
        return _empty_summary(False)

    summary = _empty_summary(True)
    try:
        # One read, not one per article: the catalog cannot change mid-run.
        catalog = {item.slug: item for item in ctx.items.all()}
        batch = limit if limit is not None else ctx.config.news_classify_batch
        for article in ctx.news.pending(batch):
            await _classify_article(ctx, article, classifier, catalog, summary)
        return summary
    finally:
        if owned and hasattr(classifier, "aclose"):
            await classifier.aclose()


async def _classify_article(ctx, article, classifier, catalog, summary) -> None:
    candidates = ctx.news.candidates_for(article.id)
    kept, skipped = triage(candidates, ctx.config.news_max_candidates_per_article)
    summary["candidates_seen"] += len(candidates)
    summary["candidates_skipped"] += skipped
    now = ctx.clock.utcnow()

    events = []
    links_per_event = []
    try:
        for candidate in kept:
            labels = await classifier.classify(
                ClassifyRequest(
                    subject=candidate.name,
                    context=candidate.context,
                    published_at=article.published_at,
                )
            )
            event = to_event(
                labels,
                candidate.name,
                article.published_at,
                ctx.config.news_strength_map,
                ctx.config.news_confidence_map,
                store_raw_json=ctx.config.news_store_raw_json,
            )
            events.append(event)
            links_per_event.append(
                build_links(
                    event.event_type,
                    event.direction,
                    candidate.slug,
                    candidate.name,
                    candidate.score,
                    catalog,
                )
            )
    except (ClassifierError, ValueError) as exc:
        # Nothing is written for a partially classified article: half its events in
        # the database would reach active_links looking complete.
        #
        # ValueError is caught deliberately broadly here: it is what to_event raises
        # for an unmapped strength/confidence label and what the EventType/
        # NewsDirection enum conversions raise for a label outside the enum. Widening
        # the try body above without narrowing this catch risks silently swallowing
        # an unrelated ValueError as a misleadingly-labelled "failed article".
        summary["failed"] += 1
        summary["errors"][article.external_id] = str(exc)
        ctx.news.mark(article.id, ArticleStatus.FAILED, when=now)
        return

    event_ids = ctx.news.insert_events(article.id, events)
    for event_id, links in zip(event_ids, links_per_event):
        summary["links"] += ctx.news.insert_links(event_id, links)
    ctx.news.mark(
        article.id,
        ArticleStatus.CLASSIFIED,
        classifier.name,
        classifier.version,
        when=now,
    )
    summary["articles"] += 1
    summary["events"] += len(events)


def reconcile_synthetic_links(ctx: AppContext) -> dict:
    """Replace predicted Prime slugs with real ones once the catalog carries them.

    No model call: the event is already classified and only its resolution changed,
    which is exactly what keeping events and links in separate tables buys.
    """
    catalog = {item.slug: item for item in ctx.items.all()}
    result = {"events": 0, "replaced": 0, "still_synthetic": 0}

    for event in ctx.news.events_with_synthetic_links():
        result["events"] += 1
        current = ctx.news.links_for_event(event.id)
        synthetic = [link for link in current if link.link_method is LinkMethod.SYNTHETIC]
        if not any(link.slug in catalog for link in synthetic):
            result["still_synthetic"] += 1
            continue

        links = []
        for link in current:
            if link.link_method is not LinkMethod.SYNTHETIC:
                links.append(link)
                continue
            links.extend(
                build_links(
                    event.event_type,
                    event.direction,
                    link.slug,
                    event.subject_raw,
                    link.link_score,
                    catalog,
                )
            )
        ctx.news.replace_links_for(event.id, links)
        result["replaced"] += 1
        # A multi-slug event can resolve one slug and still hold another: the counts
        # are not exclusive, so a partial event shows up in both.
        if any(link.link_method is LinkMethod.SYNTHETIC for link in links):
            result["still_synthetic"] += 1

    return result
