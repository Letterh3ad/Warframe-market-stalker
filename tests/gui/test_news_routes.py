from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.gui.app import build_app
from wfm.news.types import Article, NewsSource
from wfm.services import news_jobs
from wfm.services.context import AppContext

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_news_jobs():
    # news_jobs holds process-wide state; a leftover reservation from one test
    # would make the next test's POST refuse, presenting as an unrelated flake.
    news_jobs.reset()
    yield
    news_jobs.reset()


def _client(conn, config=None):
    ctx = AppContext(config or Config(news_enabled=True), conn=conn, clock=FakeClock(start_utc=NOW))
    client = TestClient(build_app(ctx))
    client.ctx = ctx
    return client


def _article(ctx, external_id="warframe_news:1"):
    return ctx.news.upsert_article(
        Article(
            source=NewsSource.WARFRAME_NEWS,
            external_id=external_id,
            url=f"https://warframe.test/{external_id}",
            title="Update 43.5",
            body="",
            published_at=NOW,
        ).hashed(),
        NOW,
    )


def test_events_feed_is_empty_on_a_fresh_corpus(conn):
    body = _client(conn).get("/news/events").json()
    assert body == {"total": 0, "limit": 50, "offset": 0, "events": []}


def test_events_feed_pages(conn):
    client = _client(conn)
    body = client.get("/news/events?limit=1&offset=0").json()
    assert body["limit"] == 1 and body["offset"] == 0


def test_an_unknown_event_type_is_an_empty_page_not_a_500(conn):
    assert _client(conn).get("/news/events?event_type=explodes").status_code == 200


def test_an_article_carries_its_events(conn):
    client = _client(conn)
    article_id = _article(client.ctx)
    body = client.get(f"/news/articles/{article_id}").json()
    assert body["article"]["title"] == "Update 43.5"
    assert body["events"] == []


def test_an_unknown_article_is_a_404(conn):
    assert _client(conn).get("/news/articles/9999").status_code == 404


def test_status_reports_the_backend_and_the_job_slot(conn):
    news_jobs.reset()
    body = _client(conn).get("/news/status").json()
    assert body["classifier"] == "none"
    assert body["job"] is None


def test_refresh_starts_a_job(conn, monkeypatch):
    news_jobs.reset()
    monkeypatch.setattr(news_jobs, "spawn", lambda ctx, kind: {"started": True, "kind": kind})
    assert _client(conn).post("/news/refresh").json() == {"started": True, "kind": "ingest"}


def test_classify_is_refused_while_a_job_runs(conn, monkeypatch):
    news_jobs.reset()
    monkeypatch.setattr(
        news_jobs, "spawn", lambda ctx, kind: {"started": False, "reason": "ingest already running"}
    )
    body = _client(conn).post("/news/classify").json()
    assert body["started"] is False and "already running" in body["reason"]
