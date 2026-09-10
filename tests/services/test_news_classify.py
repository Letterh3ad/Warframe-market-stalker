from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.models import Item
from wfm.news.classify.base import ClassifierError, FakeClassifier
from wfm.news.types import Article, ArticleStatus, Candidate, ClassifyLabels, EventType, NewsSource
from wfm.services import news_service
from wfm.services.context import AppContext

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)

CATALOG = [
    Item(slug="rage", name="Rage", url_name="rage", tags=("mod",)),
    Item(slug="adaptation", name="Adaptation", url_name="adaptation", tags=("mod",)),
    Item(slug="s0", name="S0", url_name="s0"),
    Item(slug="s1", name="S1", url_name="s1"),
    Item(slug="s2", name="S2", url_name="s2"),
    Item(slug="s3", name="S3", url_name="s3"),
    Item(slug="s4", name="S4", url_name="s4"),
]


@pytest.fixture
def ctx(conn):
    context = AppContext(Config(news_enabled=True), conn=conn, clock=FakeClock(NOW))
    context.items.upsert_many(CATALOG)
    return context


def _with_config(conn, **overrides):
    """A fresh context whose Config carries the overrides. AppContext takes the
    Config at construction, so this rebuilds rather than mutating a frozen one."""
    context = AppContext(
        Config(news_enabled=True, **overrides), conn=conn, clock=FakeClock(NOW)
    )
    context.items.upsert_many(CATALOG)
    return context


def _pending_article_with_candidates(ctx, rows, external_id="forums:1"):
    """rows: (slug, name, score, context). Writes the article and its candidates
    directly, which is what ingest leaves behind."""
    article = Article(
        source=NewsSource.FORUMS,
        external_id=external_id,
        url="https://forums.warframe.test/x",
        title="Hotfix",
        body="",
        published_at=NOW,
    ).hashed()
    article_id = ctx.news.upsert_article(article, NOW)
    ctx.news.insert_candidates(
        article_id,
        [Candidate(slug=s, name=n, score=sc, context=c, start=0, end=0)
         for s, n, sc, c in rows],
    )
    return article_id


def _labels(**kw):
    base = dict(
        event_type="vault_in", direction="up", strength="moderate",
        confidence="medium", timing="immediate", date_text=None, rationale=None,
    )
    base.update(kw)
    return ClassifyLabels(**base)


class _FailsOnFirstSubject(FakeClassifier):
    def __init__(self, bad_subject, labels):
        super().__init__(labels)
        self._bad_subject = bad_subject

    async def classify(self, req):
        self.calls.append(req)
        if req.subject == self._bad_subject:
            raise ClassifierError(f"{self._bad_subject} exploded")
        return self._labels


async def test_classifying_nothing_when_the_backend_is_none(conn):
    ctx = _with_config(conn, news_classifier="none")
    summary = await news_service.classify(ctx)
    assert summary["enabled"] is False
    assert summary["articles"] == 0


async def test_a_pending_article_becomes_classified_with_events_and_links(ctx):
    article_id = _pending_article_with_candidates(ctx, [("rage", "Rage", 1.0,
        "Reduced Rage's energy conversion.")])
    fake = FakeClassifier(_labels(event_type="nerf", direction="down"))

    summary = await news_service.classify(ctx, classifier=fake)

    assert summary["articles"] == 1 and summary["events"] == 1
    assert summary["links"] >= 1
    stored = ctx.news.events_for_article(article_id)
    assert stored[0].event_type is EventType.NERF
    assert ctx.news.count_articles(ArticleStatus.CLASSIFIED) == 1


async def test_the_classifier_name_and_version_are_stamped_on_the_article(ctx):
    _pending_article_with_candidates(ctx, [("rage", "Rage", 1.0, "Reduced Rage.")])
    await news_service.classify(ctx, classifier=FakeClassifier(_labels()))
    article = ctx.news.recent_articles(limit=1)[0]
    assert (article.classifier_name, article.classifier_version) == ("fake", "1")


async def test_one_call_is_made_per_kept_candidate_not_per_article(ctx):
    _pending_article_with_candidates(
        ctx,
        [
            ("rage", "Rage", 1.0, "Reduced Rage's conversion."),
            ("adaptation", "Adaptation", 1.0, "Increased Adaptation's cap."),
        ],
    )
    fake = FakeClassifier(_labels())
    await news_service.classify(ctx, classifier=fake)
    assert len(fake.calls) == 2
    assert {c.subject for c in fake.calls} == {"Rage", "Adaptation"}


async def test_the_prompt_carries_the_stored_context_and_publish_date(ctx):
    _pending_article_with_candidates(ctx, [("rage", "Rage", 1.0, "Reduced Rage.")])
    fake = FakeClassifier(_labels())
    await news_service.classify(ctx, classifier=fake)
    assert fake.calls[0].context == "Reduced Rage."
    assert fake.calls[0].published_at is not None


async def test_triage_keeps_a_zero_signal_candidate_out_of_the_model(ctx):
    _pending_article_with_candidates(
        ctx, [("rage", "Rage", 1.0, "Fixed Rage not applying to Operator damage.")]
    )
    fake = FakeClassifier(_labels())
    summary = await news_service.classify(ctx, classifier=fake)
    assert fake.calls == []
    assert summary["candidates_skipped"] == 1
    # Nothing to classify is not a failure, and it must not be retried forever.
    assert ctx.news.count_articles(ArticleStatus.PENDING) == 0


async def test_the_cap_bounds_the_calls_per_article(conn):
    ctx = _with_config(conn, news_max_candidates_per_article=2)
    _pending_article_with_candidates(
        ctx, [(f"s{i}", f"S{i}", 1.0, "Increased its damage.") for i in range(5)]
    )
    fake = FakeClassifier(_labels())
    summary = await news_service.classify(ctx, classifier=fake)
    assert len(fake.calls) == 2
    assert summary["candidates_skipped"] == 3


async def test_a_dead_backend_fails_the_article_and_writes_nothing(ctx):
    article_id = _pending_article_with_candidates(
        ctx, [("rage", "Rage", 1.0, "Reduced Rage.")]
    )
    fake = FakeClassifier(_labels(), fail_with=ClassifierError("ollama is down"))

    summary = await news_service.classify(ctx, classifier=fake)

    assert summary["failed"] == 1 and summary["events"] == 0
    assert ctx.news.events_for_article(article_id) == []
    assert ctx.news.count_articles(ArticleStatus.FAILED) == 1


async def test_one_dead_article_does_not_stop_the_next(ctx):
    _pending_article_with_candidates(
        ctx, [("rage", "Rage", 1.0, "Reduced Rage.")], external_id="a"
    )
    _pending_article_with_candidates(
        ctx, [("adaptation", "Adaptation", 1.0, "Increased it.")], external_id="b"
    )
    fake = _FailsOnFirstSubject("Rage", _labels())
    summary = await news_service.classify(ctx, classifier=fake)
    assert summary["failed"] == 1 and summary["articles"] == 1


async def test_the_batch_size_bounds_how_many_articles_one_run_takes(conn):
    ctx = _with_config(conn, news_classify_batch=1)
    _pending_article_with_candidates(ctx, [("rage", "Rage", 1.0, "Reduced it.")],
                                     external_id="a")
    _pending_article_with_candidates(ctx, [("rage", "Rage", 1.0, "Reduced it.")],
                                     external_id="b")
    summary = await news_service.classify(ctx, classifier=FakeClassifier(_labels()))
    assert summary["articles"] == 1
    assert ctx.news.count_articles(ArticleStatus.PENDING) == 1


async def test_an_unmapped_label_fails_the_article_rather_than_storing_a_zero(conn):
    ctx = _with_config(conn, news_strength_map={"minor": 0.3})
    _pending_article_with_candidates(ctx, [("rage", "Rage", 1.0, "Reduced Rage.")])
    summary = await news_service.classify(
        ctx, classifier=FakeClassifier(_labels(strength="major"))
    )
    assert summary["failed"] == 1


async def test_raw_json_is_stored_only_when_configured(conn):
    ctx = _with_config(conn, news_store_raw_json=True)
    article_id = _pending_article_with_candidates(
        ctx, [("rage", "Rage", 1.0, "Reduced Rage.")]
    )
    await news_service.classify(ctx, classifier=FakeClassifier(_labels()))
    assert ctx.news.events_for_article(article_id)[0].raw_json is not None


def test_build_classifier_returns_none_when_disabled(conn):
    assert news_service.build_classifier(_with_config(conn, news_classifier="none")) is None


def test_build_classifier_builds_the_configured_backend(conn):
    clf = news_service.build_classifier(
        _with_config(conn, news_classifier="ollama", news_model="qwen3:4b")
    )
    assert clf.name == "ollama" and clf.version == "qwen3:4b"


def test_build_classifier_rejects_an_unknown_backend_name(conn):
    ctx = _with_config(conn, news_classifier="fake")
    with pytest.raises(ValueError, match="fake"):
        news_service.build_classifier(ctx)


def test_status_reports_the_classifier_and_the_counts(conn):
    got = news_service.status(_with_config(conn, news_classifier="ollama",
                                           news_model="qwen3:4b"))
    assert got["classifier"] == "ollama:qwen3:4b"
    assert got["classified"] == 0 and got["failed"] == 0
