from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.models import Item
from wfm.news.types import Article, EventType, ExtractedEvent, ItemLink, LinkMethod, NewsDirection, NewsSource
from wfm.services import news_service, sync_service
from wfm.services.context import AppContext

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def ctx(conn):
    return AppContext(Config(news_enabled=True), conn=conn, clock=FakeClock(NOW))


def _classified_event_with_synthetic_link(
    ctx, slug, event_type=EventType.PRIME_ACCESS, external_id="forums:1"
):
    article = Article(
        source=NewsSource.FORUMS,
        external_id=external_id,
        url=f"https://forums.warframe.test/{external_id}",
        title="Steflos Prime Access",
        body="",
        published_at=NOW,
    ).hashed()
    article_id = ctx.news.upsert_article(article, NOW)
    event = ExtractedEvent(
        event_type=event_type,
        subject_raw="Steflos Prime",
        direction=NewsDirection.DOWN,
        strength=0.6,
        confidence=0.8,
    )
    (event_id,) = ctx.news.insert_events(article_id, [event])
    ctx.news.insert_links(
        event_id,
        [
            ItemLink(
                slug=slug,
                rank=0,
                link_method=LinkMethod.SYNTHETIC,
                link_score=1.0,
                direction=NewsDirection.DOWN,
                weight=1.0,
            )
        ],
    )
    return event_id


def _classified_event_with_link(ctx, slug, method, external_id="forums:2"):
    article = Article(
        source=NewsSource.FORUMS,
        external_id=external_id,
        url=f"https://forums.warframe.test/{external_id}",
        title="Hotfix",
        body="",
        published_at=NOW,
    ).hashed()
    article_id = ctx.news.upsert_article(article, NOW)
    event = ExtractedEvent(
        event_type=EventType.NERF,
        subject_raw="Rage",
        direction=NewsDirection.DOWN,
        strength=0.6,
        confidence=0.8,
    )
    (event_id,) = ctx.news.insert_events(article_id, [event])
    ctx.news.insert_links(
        event_id,
        [
            ItemLink(
                slug=slug,
                rank=0,
                link_method=method,
                link_score=1.0,
                direction=NewsDirection.DOWN,
                weight=1.0,
            )
        ],
    )
    return event_id


def _classified_event_with_synthetic_links(
    ctx, slugs, event_type=EventType.PRIME_ACCESS, external_id="forums:3"
):
    article = Article(
        source=NewsSource.FORUMS,
        external_id=external_id,
        url=f"https://forums.warframe.test/{external_id}",
        title="Steflos Prime Access",
        body="",
        published_at=NOW,
    ).hashed()
    article_id = ctx.news.upsert_article(article, NOW)
    event = ExtractedEvent(
        event_type=event_type,
        subject_raw="Steflos Prime",
        direction=NewsDirection.DOWN,
        strength=0.6,
        confidence=0.8,
    )
    (event_id,) = ctx.news.insert_events(article_id, [event])
    ctx.news.insert_links(
        event_id,
        [
            ItemLink(
                slug=slug,
                rank=0,
                link_method=LinkMethod.SYNTHETIC,
                link_score=1.0,
                direction=NewsDirection.DOWN,
                weight=1.0,
            )
            for slug in slugs
        ],
    )
    return event_id


async def _sync_with(ctx, changed, monkeypatch):
    from wfm.sync.catalog import CatalogSyncResult

    async def fake_sync_catalog(*args, **kwargs):
        return CatalogSyncResult(
            changed, "v1", ctx.items.count(), requests_spent=1
        )

    monkeypatch.setattr(sync_service, "sync_catalog", fake_sync_catalog)
    return await sync_service.sync(ctx)


async def test_a_synthetic_link_survives_while_the_slug_is_unknown(ctx):
    event_id = _classified_event_with_synthetic_link(ctx, "steflos_prime_set")
    got = news_service.reconcile_synthetic_links(ctx)
    assert got == {"events": 1, "replaced": 0, "still_synthetic": 1}
    assert ctx.news.links_for_event(event_id)[0].link_method is LinkMethod.SYNTHETIC


async def test_a_released_slug_replaces_the_prediction_and_expands_the_set(ctx):
    event_id = _classified_event_with_synthetic_link(ctx, "steflos_prime_set")
    ctx.items.upsert_many(
        [
            Item(slug="steflos_prime_set", name="Steflos Prime Set", url_name="a",
                 tags=("set", "prime", "weapon"), is_set=True),
            Item(slug="steflos_prime_barrel", name="Steflos Prime Barrel",
                 url_name="b", tags=("component", "prime")),
        ]
    )

    got = news_service.reconcile_synthetic_links(ctx)

    assert got["replaced"] == 1
    links = {l.slug: l for l in ctx.news.links_for_event(event_id)}
    assert links["steflos_prime_set"].link_method is LinkMethod.EXACT
    # Set expansion, which a synthetic link could never do: there was no catalog row
    # to expand from.
    assert links["steflos_prime_barrel"].link_method is LinkMethod.SET_EXPANSION


def test_the_direction_still_comes_from_the_event_type(ctx):
    event_id = _classified_event_with_synthetic_link(
        ctx, "steflos_prime_set", event_type=EventType.PRIME_ACCESS
    )
    ctx.items.upsert_many([Item(slug="steflos_prime_set", name="Steflos Prime Set",
                                url_name="a", tags=("set", "prime"), is_set=True)])
    news_service.reconcile_synthetic_links(ctx)
    assert ctx.news.links_for_event(event_id)[0].direction is NewsDirection.DOWN


def test_an_event_with_no_synthetic_link_is_not_touched(ctx):
    event_id = _classified_event_with_link(ctx, "rage", LinkMethod.EXACT)
    before = ctx.news.links_for_event(event_id)
    assert news_service.reconcile_synthetic_links(ctx)["events"] == 0
    assert ctx.news.links_for_event(event_id) == before


async def test_reconciliation_run_twice_does_not_duplicate_links(ctx):
    event_id = _classified_event_with_synthetic_link(ctx, "steflos_prime_set")
    ctx.items.upsert_many(
        [
            Item(slug="steflos_prime_set", name="Steflos Prime Set", url_name="a",
                 tags=("set", "prime", "weapon"), is_set=True),
            Item(slug="steflos_prime_barrel", name="Steflos Prime Barrel",
                 url_name="b", tags=("component", "prime")),
        ]
    )
    news_service.reconcile_synthetic_links(ctx)
    first = ctx.news.links_for_event(event_id)
    got = news_service.reconcile_synthetic_links(ctx)
    assert got["events"] == 0
    assert ctx.news.links_for_event(event_id) == first


async def test_a_partially_resolved_event_is_counted_as_both(ctx):
    event_id = _classified_event_with_synthetic_links(
        ctx, ["steflos_prime_set", "unreleased_prime_set"]
    )
    ctx.items.upsert_many(
        [
            Item(slug="steflos_prime_set", name="Steflos Prime Set", url_name="a",
                 tags=("set", "prime", "weapon"), is_set=True),
        ]
    )

    got = news_service.reconcile_synthetic_links(ctx)

    # Not mutually exclusive: one slug resolved (replaced) while the sibling on the
    # same event is still a prediction (still_synthetic), and the honest report
    # says both.
    assert got == {"events": 1, "replaced": 1, "still_synthetic": 1}
    links = {l.slug: l for l in ctx.news.links_for_event(event_id)}
    assert links["steflos_prime_set"].link_method is LinkMethod.EXACT
    assert links["unreleased_prime_set"].link_method is LinkMethod.SYNTHETIC


async def test_a_failed_reconciliation_is_retried_on_the_next_no_op_sync(ctx, monkeypatch):
    """The stranding scenario: sync_catalog already committed its write (and moved the
    cursor) before reconcile_synthetic_links raises. Gating on `changed` alone would
    mean the next sync sees no version change and never retries -- the event would
    sit SYNTHETIC forever. Gating on "is there work" means the next sync, even a
    no-op one, tries again and heals it.
    """
    _classified_event_with_synthetic_link(ctx, "steflos_prime_set")
    real_reconcile = news_service.reconcile_synthetic_links
    calls = {"n": 0}

    def flaky(c):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db is locked")
        return real_reconcile(c)

    monkeypatch.setattr(news_service, "reconcile_synthetic_links", flaky)

    with pytest.raises(RuntimeError):
        await _sync_with(ctx, changed=True, monkeypatch=monkeypatch)

    # The slug ships between the failed attempt and the next sync.
    ctx.items.upsert_many(
        [Item(slug="steflos_prime_set", name="Steflos Prime Set", url_name="a",
              tags=("set", "prime"), is_set=True)]
    )

    result = await _sync_with(ctx, changed=False, monkeypatch=monkeypatch)

    assert result["news_reconciled"]["replaced"] == 1
    assert calls["n"] == 2


async def test_sync_reconciles_only_when_the_catalog_changed(ctx, monkeypatch):
    calls = []
    monkeypatch.setattr(
        news_service, "reconcile_synthetic_links", lambda c: calls.append(c) or {}
    )
    await _sync_with(ctx, changed=False, monkeypatch=monkeypatch)
    assert calls == []
    await _sync_with(ctx, changed=True, monkeypatch=monkeypatch)
    assert len(calls) == 1


async def test_a_dry_run_sync_reconciles_nothing(ctx):
    result = await sync_service.sync(ctx, dry_run=True)
    assert "news_reconciled" not in result or result["news_reconciled"] is None
