from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from tests.fakes.news import FakeSource
from wfm.config import Config
from wfm.models import Item
from wfm.news.types import Article, ArticleStatus, NewsSource
from wfm.services import news_service
from wfm.services.context import AppContext

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)

CATALOG = [
    Item(slug="archon_continuity", name="Archon Continuity", url_name="archon_continuity"),
    Item(slug="rage", name="Rage", url_name="rage"),
    Item(slug="mesa_prime_set", name="Mesa Prime Set", url_name="mesa_prime_set", is_set=True),
]


@pytest.fixture
def ctx(conn):
    context = AppContext(Config(news_enabled=True), conn=conn, clock=FakeClock(NOW))
    context.items.upsert_many(CATALOG)
    return context


def article(external_id="forums:1", title="Hotfix", body="Fixed Archon Continuity."):
    return Article(
        source=NewsSource.FORUMS,
        external_id=external_id,
        url=f"https://forums.warframe.test/{external_id}",
        title=title,
        body=body,
        published_at=NOW,
    ).hashed()


async def test_ingest_stores_an_article_and_its_candidates(ctx):
    result = await news_service.ingest(ctx, sources=[FakeSource([article()])])

    assert result["articles_stored"] == 1
    assert result["candidates_stored"] == 1
    (stored,) = ctx.news.recent_articles()
    assert stored.external_id == "forums:1"
    (candidate,) = ctx.news.candidates_for(stored.id)
    assert candidate.slug == "archon_continuity"


async def test_the_body_is_never_persisted(ctx):
    await news_service.ingest(ctx, sources=[FakeSource([article()])])
    (stored,) = ctx.news.recent_articles()
    assert stored.body == ""


async def test_an_article_the_gate_finds_nothing_in_is_marked_no_match(ctx):
    source = FakeSource([article(body="A patch note about nothing tradeable.")])
    result = await news_service.ingest(ctx, sources=[source])

    assert result["no_match"] == 1
    (stored,) = ctx.news.recent_articles()
    assert stored.status is ArticleStatus.NO_MATCH
    assert ctx.news.pending() == []


async def test_a_matched_article_stays_pending_for_the_classifier(ctx):
    await news_service.ingest(ctx, sources=[FakeSource([article()])])
    assert [a.external_id for a in ctx.news.pending()] == ["forums:1"]


async def test_the_excerpt_is_the_matched_context_not_the_whole_body(ctx):
    body = "Filler. " * 60 + "Fixed Archon Continuity not applying. " + "More filler. " * 60
    await news_service.ingest(ctx, sources=[FakeSource([article(body=body)])])

    (stored,) = ctx.news.recent_articles()
    assert stored.excerpt is not None
    assert "Archon Continuity" in stored.excerpt
    assert len(stored.excerpt) < len(body)


async def test_an_unchanged_article_is_skipped_on_the_second_poll(ctx):
    source = FakeSource([article()])
    await news_service.ingest(ctx, sources=[source])
    result = await news_service.ingest(ctx, sources=[source])

    assert result["articles_stored"] == 0
    assert result["unchanged"] == 1
    assert len(ctx.news.recent_articles()) == 1


async def test_an_edited_article_is_regated_and_stale_candidates_are_dropped(ctx):
    await news_service.ingest(ctx, sources=[FakeSource([article()])])
    edited = article(body="Fixed Rage instead.")
    await news_service.ingest(ctx, sources=[FakeSource([edited])])

    (stored,) = ctx.news.recent_articles()
    assert [c.slug for c in ctx.news.candidates_for(stored.id)] == ["rage"]


async def test_one_failing_source_does_not_lose_the_others(ctx):
    good = FakeSource([article()])
    bad = FakeSource([], name=NewsSource.REDDIT, fail_with=RuntimeError("upstream is down"))

    result = await news_service.ingest(ctx, sources=[bad, good])

    assert result["articles_stored"] == 1
    assert result["errors"] == {"reddit": "upstream is down"}


async def test_ingest_reports_per_source_counts(ctx):
    forums = FakeSource([article()], name=NewsSource.FORUMS)
    wf = FakeSource(
        [article(external_id="warframe_news:x", body="Mesa Prime returns.")],
        name=NewsSource.WARFRAME_NEWS,
    )
    result = await news_service.ingest(ctx, sources=[forums, wf])

    assert result["sources"]["forums"]["fetched"] == 1
    assert result["sources"]["warframe_news"]["fetched"] == 1
    assert result["articles_stored"] == 2


async def test_ingest_does_nothing_when_news_is_disabled(conn):
    ctx = AppContext(Config(news_enabled=False), conn=conn, clock=FakeClock(NOW))
    source = FakeSource([article()])
    result = await news_service.ingest(ctx, sources=[source])

    assert result["enabled"] is False
    assert source.calls == 0
    assert ctx.news.recent_articles() == []


async def test_force_runs_even_when_news_is_disabled(conn):
    ctx = AppContext(Config(news_enabled=False), conn=conn, clock=FakeClock(NOW))
    ctx.items.upsert_many(CATALOG)
    result = await news_service.ingest(ctx, sources=[FakeSource([article()])], force=True)
    assert result["articles_stored"] == 1


async def test_build_sources_honours_the_configured_list(ctx):
    fetcher = object()
    names = [s.name for s in news_service.build_sources(ctx, fetcher)]
    assert names == [NewsSource.WARFRAME_NEWS, NewsSource.FORUMS]


async def test_build_sources_ignores_an_unknown_name(conn):
    ctx = AppContext(
        Config(news_sources=("forums", "twitter")), conn=conn, clock=FakeClock(NOW)
    )
    assert [s.name for s in news_service.build_sources(ctx, object())] == [NewsSource.FORUMS]
    # The discard is surfaced, not silent.
    assert news_service.unknown_sources(ctx) == ["twitter"]


def test_status_reports_the_corpus(ctx):
    assert news_service.status(ctx) == {
        "enabled": True,
        "sources": ["warframe_news", "forums"],
        "unknown_sources": [],
        "classifier": "none",
        "articles": 0,
        "pending": 0,
        "classified": 0,
        "failed": 0,
    }


def test_status_lists_only_resolving_sources_and_flags_the_rest(conn):
    ctx = AppContext(
        Config(news_sources=("warframe_news", "twitter", "forums")),
        conn=conn,
        clock=FakeClock(NOW),
    )
    reported = news_service.status(ctx)
    assert reported["sources"] == ["warframe_news", "forums"]
    assert reported["unknown_sources"] == ["twitter"]


async def test_ingest_summary_flags_unknown_configured_sources(conn):
    ctx = AppContext(
        Config(news_enabled=True, news_sources=("twitter",)),
        conn=conn,
        clock=FakeClock(NOW),
    )
    ctx.items.upsert_many(CATALOG)
    result = await news_service.ingest(ctx)
    assert result["unknown_sources"] == ["twitter"]


async def test_status_counts_what_ingest_stored(ctx):
    await news_service.ingest(ctx, sources=[FakeSource([article()])])
    reported = news_service.status(ctx)
    assert reported["articles"] == 1
    assert reported["pending"] == 1


def test_status_reports_the_model_of_the_configured_backend(conn):
    # news_model is the Ollama tag and survives a switch to claude, so reporting the
    # first truthy of the two names a model the run will never call.
    ctx = AppContext(
        Config(
            news_classifier="claude",
            news_model="qwen3:4b-instruct-2507-q8_0",
            news_claude_model="claude-haiku-4-5",
        ),
        conn=conn,
        clock=FakeClock(NOW),
    )
    assert news_service.status(ctx)["classifier"] == "claude:claude-haiku-4-5"


def test_status_reports_the_ollama_tag_under_the_ollama_backend(conn):
    ctx = AppContext(
        Config(news_classifier="ollama", news_model="qwen3:4b"),
        conn=conn,
        clock=FakeClock(NOW),
    )
    assert news_service.status(ctx)["classifier"] == "ollama:qwen3:4b"
