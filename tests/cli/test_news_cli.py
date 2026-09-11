from wfm.cli.main import build_parser


def test_news_is_a_registered_subcommand():
    args = build_parser().parse_args(["news", "status"])
    assert args.command == "news"
    assert args.news_command == "status"


def test_ingest_is_the_other_mode():
    args = build_parser().parse_args(["news", "ingest"])
    assert args.news_command == "ingest"
    assert args.force is False


def test_ingest_takes_a_force_flag():
    args = build_parser().parse_args(["news", "ingest", "--force"])
    assert args.force is True


def test_news_without_a_mode_is_a_parse_error(capsys):
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args(["news"])


def test_classify_is_a_mode():
    args = build_parser().parse_args(["news", "classify"])
    assert args.news_command == "classify"
    assert args.limit is None  # falls back to news_classify_batch


def test_classify_takes_a_limit():
    args = build_parser().parse_args(["news", "classify", "--limit", "3"])
    assert args.limit == 3


def test_a_non_numeric_limit_is_a_parse_error():
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args(["news", "classify", "--limit", "lots"])


def test_relink_is_a_mode():
    args = build_parser().parse_args(["news", "relink"])
    assert args.news_command == "relink"


# --- dispatch ---------------------------------------------------------------
#
# Everything above is parser-only. These exercise `run` itself: a typo in one of the
# "classify" / "relink" / "status" strings would otherwise fall through to `ingest`
# and hit the network with nobody noticing.

import json
from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.cli.main import main
from wfm.config import Config
from wfm.news.classify.base import ClassifierError
from wfm.services import news_service
from wfm.services.context import AppContext

START = datetime(2026, 8, 27, tzinfo=timezone.utc)


@pytest.fixture
def wired(conn, monkeypatch):
    ctx = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=START))
    monkeypatch.setattr("wfm.cli.context_factory.build", lambda args: ctx)
    return ctx


def test_status_dispatches_to_status(wired, capsys):
    assert main(["--json", "news", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["classifier"] == "none"


def test_classify_dispatches_to_classify(wired, capsys, monkeypatch):
    seen = {}

    async def fake_classify(ctx, limit=None):
        seen["limit"] = limit
        return dict(news_service._empty_summary(True), articles=2)

    monkeypatch.setattr(news_service, "classify", fake_classify)
    assert main(["--json", "news", "classify", "--limit", "3"]) == 0
    assert seen["limit"] == 3
    assert json.loads(capsys.readouterr().out)["articles"] == 2


def test_relink_dispatches_to_reconcile(wired, capsys, monkeypatch):
    monkeypatch.setattr(
        news_service, "reconcile_synthetic_links",
        lambda ctx: {"events": 1, "replaced": 1, "still_synthetic": 0},
    )
    assert main(["--json", "news", "relink"]) == 0
    assert json.loads(capsys.readouterr().out)["replaced"] == 1


def test_an_unknown_classifier_name_exits_non_zero(conn, capsys, monkeypatch):
    ctx = AppContext(
        Config(news_classifier="olama"), conn=conn, clock=FakeClock(start_utc=START)
    )
    monkeypatch.setattr("wfm.cli.context_factory.build", lambda args: ctx)
    assert main(["news", "classify"]) == 1
    assert "olama" in capsys.readouterr().err


def test_a_missing_optional_backend_exits_non_zero(wired, capsys, monkeypatch):
    # ClassifierError, not ValueError: this is what ClaudeClassifier.__init__ raises
    # when the optional `anthropic` extra is absent, which is this environment.
    def boom(ctx):
        raise ClassifierError("the claude backend needs the optional dependency")

    monkeypatch.setattr(news_service, "build_classifier", boom)
    assert main(["news", "classify"]) == 1
    assert "optional dependency" in capsys.readouterr().err


def test_a_run_with_failures_exits_non_zero(wired, capsys, monkeypatch):
    # Failure is terminal on this branch: nothing requeues a FAILED article, so a
    # silent exit 0 is how a user learns weeks later that nothing was classified.
    async def fake_classify(ctx, limit=None):
        return dict(news_service._empty_summary(True), failed=2)

    monkeypatch.setattr(news_service, "classify", fake_classify)
    assert main(["--json", "news", "classify"]) == 1
    assert json.loads(capsys.readouterr().out)["failed"] == 2


def test_a_disabled_classifier_still_exits_zero(wired, capsys):
    assert main(["--json", "news", "classify"]) == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is False
