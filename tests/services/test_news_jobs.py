"""One job at a time, whoever asks.

The daemon tick and the GUI buttons run the same two jobs. Two classify runs over
one pending batch would write every event twice, because both read pending() before
either marks anything.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.services import news_jobs, news_service
from wfm.services.context import AppContext

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def ctx(conn):
    return AppContext(Config(news_enabled=True), conn=conn, clock=FakeClock(start_utc=NOW))


@pytest.fixture(autouse=True)
def reset_jobs():
    news_jobs.reset()
    yield
    news_jobs.reset()


async def test_a_job_reports_its_result_and_then_clears(ctx, monkeypatch):
    async def fake_ingest(context):
        return {"articles_stored": 3}

    monkeypatch.setattr(news_service, "ingest", fake_ingest)

    result = await news_jobs.run(ctx, "ingest")

    assert result == {"articles_stored": 3}
    assert news_jobs.current()["running"] is False
    assert news_jobs.current()["kind"] == "ingest"


async def test_a_second_start_is_refused_while_one_is_in_flight(ctx, monkeypatch):
    release = asyncio.Event()

    async def slow_ingest(context):
        await release.wait()
        return {"articles_stored": 0}

    monkeypatch.setattr(news_service, "ingest", slow_ingest)

    first = news_jobs.spawn(ctx, "ingest")
    await asyncio.sleep(0)
    second = news_jobs.spawn(ctx, "classify")

    assert first["started"] is True
    assert second["started"] is False
    assert "ingest" in second["reason"]

    release.set()
    await news_jobs.wait()


async def test_the_tick_and_a_spawned_job_cannot_overlap(ctx, monkeypatch):
    order = []
    release = asyncio.Event()

    async def slow_classify(context, on_progress=None):
        order.append("spawned-start")
        await release.wait()
        order.append("spawned-end")
        return {"articles": 1, "events": 0}

    monkeypatch.setattr(news_service, "classify", slow_classify)
    news_jobs.spawn(ctx, "classify")
    await asyncio.sleep(0)

    # The tick's path: run() awaits, and must not start a second classify.
    refused = await news_jobs.run(ctx, "classify")
    assert refused["started"] is False

    release.set()
    await news_jobs.wait()
    assert order == ["spawned-start", "spawned-end"]


async def test_progress_is_visible_while_a_classify_runs(ctx, monkeypatch):
    seen = []

    async def classify_with_progress(context, on_progress=None):
        on_progress("news:1", 1)
        seen.append(news_jobs.current()["done"])
        on_progress("news:2", 2)
        seen.append(news_jobs.current()["done"])
        return {"articles": 2, "events": 0}

    monkeypatch.setattr(news_service, "classify", classify_with_progress)
    await news_jobs.run(ctx, "classify")

    assert seen == [1, 2]


async def test_a_failing_job_records_the_error_and_releases_the_lock(ctx, monkeypatch):
    async def boom(context):
        raise RuntimeError("warframe.com is down")

    monkeypatch.setattr(news_service, "ingest", boom)

    result = await news_jobs.run(ctx, "ingest")

    assert "warframe.com is down" in result["error"]
    assert news_jobs.current()["running"] is False
    # The lock must be free, or one outage wedges the feature until a restart.
    assert news_jobs.spawn(ctx, "ingest")["started"] is True
    await news_jobs.wait()


def test_an_unknown_kind_is_refused_before_anything_starts(ctx):
    assert news_jobs.spawn(ctx, "reticulate")["started"] is False
