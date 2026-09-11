"""The daemon's news tick: the half of build-order step 5 that never shipped.

Ingest was reachable only from the CLI, so the corpus grew only when someone typed
`wfm news ingest` by hand. These tests pin the tick's three promises: it runs on its
own interval, a news failure never stops the price loop, and a stop is heard during a
long classify rather than after it.
"""

from datetime import datetime, timezone

import pytest

from tests.fakes.api import StubClient
from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.daemon.runner import Daemon
from wfm.models import Item
from wfm.news.types import Article, ArticleStatus, NewsSource
from wfm.services import news_service
from wfm.services.context import AppContext

START = datetime(2026, 9, 11, 5, 0, tzinfo=timezone.utc)
VERSIONS = {"collections": {"items": "v42"}}


@pytest.fixture
def ctx(conn):
    client = StubClient({"/versions": VERSIONS, "/items": [], "/statistics": {}})
    context = AppContext(
        Config(news_enabled=True, news_classifier="ollama", news_model="m"),
        conn=conn,
        clock=FakeClock(start_utc=START),
        client=client,
    )
    context.items.upsert_many([Item(slug="a", name="A", url_name="a", tags=("mod",))])
    context.daemon_state.mark_started(pid=1, when=START)
    context.daemon_state.mark_daily_done("sweep", START.date())
    context.daemon_state.mark_daily_done("digest", START.date())
    return context


def _spy(calls, name, result):
    async def fake(ctx, *a, **kw):
        calls.append(name)
        return result
    return fake


def _patch_news(monkeypatch, calls, ingested=0, summary=None):
    monkeypatch.setattr(
        news_service, "ingest", _spy(calls, "ingest", {"articles_stored": ingested})
    )
    monkeypatch.setattr(
        news_service,
        "classify",
        _spy(calls, "classify", summary or {"articles": 0, "events": 0}),
    )


async def test_the_tick_ingests_and_classifies_without_anyone_typing_a_command(
    ctx, monkeypatch
):
    calls = []
    _patch_news(monkeypatch, calls, ingested=2, summary={"articles": 2, "events": 5})
    await Daemon(ctx).run(max_iterations=1)
    assert calls == ["ingest", "classify"]


async def test_the_tick_does_not_run_again_inside_its_interval(ctx, monkeypatch):
    calls = []
    _patch_news(monkeypatch, calls)
    await Daemon(ctx).run(max_iterations=3)
    assert calls == ["ingest", "classify"]


async def test_the_tick_runs_again_once_the_interval_has_passed(ctx, monkeypatch):
    calls = []
    _patch_news(monkeypatch, calls)
    daemon = Daemon(ctx)
    await daemon.run(max_iterations=1)
    ctx.clock.advance(ctx.config.news_poll_interval_s)
    await daemon.run(max_iterations=1)
    assert calls == ["ingest", "classify", "ingest", "classify"]


async def test_news_is_skipped_entirely_when_it_is_disabled(conn, monkeypatch):
    calls = []
    context = AppContext(
        Config(news_enabled=False), conn=conn, clock=FakeClock(start_utc=START)
    )
    context.daemon_state.mark_started(pid=1, when=START)
    context.daemon_state.mark_daily_done("sweep", START.date())
    context.daemon_state.mark_daily_done("digest", START.date())
    _patch_news(monkeypatch, calls)
    await Daemon(context).run(max_iterations=1)
    assert calls == []


async def test_nothing_is_classified_when_no_backend_is_configured(conn, monkeypatch):
    calls = []
    context = AppContext(
        Config(news_enabled=True, news_classifier="none"),
        conn=conn,
        clock=FakeClock(start_utc=START),
    )
    context.daemon_state.mark_started(pid=1, when=START)
    context.daemon_state.mark_daily_done("sweep", START.date())
    context.daemon_state.mark_daily_done("digest", START.date())
    _patch_news(monkeypatch, calls)
    await Daemon(context).run(max_iterations=1)
    assert calls == ["ingest"]


async def test_a_dead_news_upstream_does_not_halt_the_price_loop(ctx, monkeypatch):
    async def boom(ctx, *a, **kw):
        raise RuntimeError("warframe.com is down")

    monkeypatch.setattr(news_service, "ingest", boom)
    report = await Daemon(ctx).run(max_iterations=1)
    # The loop's own catch halts; the news catch must not.
    assert report.halted is False
    assert ctx.daemon_state.get()["status"] == "running"


async def test_a_dead_classifier_does_not_halt_the_price_loop(ctx, monkeypatch):
    calls = []
    _patch_news(monkeypatch, calls)

    async def boom(ctx, *a, **kw):
        raise RuntimeError("ollama is not running")

    monkeypatch.setattr(news_service, "classify", boom)
    report = await Daemon(ctx).run(max_iterations=1)
    assert report.halted is False


async def test_a_failed_tick_waits_out_its_interval_rather_than_retrying_hot(
    ctx, monkeypatch
):
    attempts = []

    async def boom(ctx, *a, **kw):
        attempts.append(1)
        raise RuntimeError("down")

    monkeypatch.setattr(news_service, "ingest", boom)
    await Daemon(ctx).run(max_iterations=3)
    assert len(attempts) == 1


async def test_failed_articles_are_requeued_before_a_classify_run(ctx, monkeypatch):
    calls = []
    _patch_news(monkeypatch, calls)
    article = Article(
        source=NewsSource.WARFRAME_NEWS,
        external_id="warframe_news:1",
        url="https://warframe.test/1",
        title="t",
        body="",
        published_at=START,
    ).hashed()
    article_id = ctx.news.upsert_article(article, START)
    ctx.news.mark(article_id, ArticleStatus.FAILED, "ollama", "m", when=START)

    await Daemon(ctx).run(max_iterations=1)

    assert ctx.news.count_articles(ArticleStatus.PENDING) == 1
    assert ctx.news.count_articles(ArticleStatus.FAILED) == 0


async def test_a_stop_during_a_long_classify_is_heard_before_it_finishes(
    ctx, monkeypatch
):
    seen = []

    async def slow_classify(context, classifier=None, limit=None, on_progress=None):
        for n in range(1, 4):
            if n == 2:
                context.daemon_state.request_stop(START)
            on_progress(f"article:{n}", n)
            seen.append(n)
        return {"articles": 3, "events": 0}

    monkeypatch.setattr(news_service, "ingest", _spy([], "ingest", {"articles_stored": 0}))
    monkeypatch.setattr(news_service, "classify", slow_classify)

    await Daemon(ctx).run(max_iterations=1)

    # The third article is never reached: the callback raised on the one after the
    # stop was recorded.
    assert seen == [1]
    assert ctx.daemon_state.get()["status"] == "stopped"
