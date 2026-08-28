import json
from datetime import datetime, timezone

import pytest

from tests.fakes.api import StubClient
from tests.fakes.clock import FakeClock
from wfm.cli.main import main
from wfm.config import Config
from wfm.models import Item
from wfm.services.context import AppContext

START = datetime(2026, 8, 27, tzinfo=timezone.utc)
VERSIONS = {"collections": {"items": "v42"}}
ITEMS = [{"slug": "a", "i18n": {"en": {"name": "Alpha"}}, "tags": ["mod"], "maxRank": 10}]


@pytest.fixture
def wired(conn, monkeypatch):
    client = StubClient({"/versions": VERSIONS, "/items": ITEMS})
    ctx = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=START), client=client)
    ctx.items.upsert_many([Item(slug="a", name="Alpha", url_name="a", max_rank=10,
                                canonical_rank=10)])
    monkeypatch.setattr("wfm.cli.context_factory.build", lambda args: ctx)
    return ctx


def test_search_prints_a_table(wired, capsys):
    assert main(["search", "Alpha"]) == 0
    assert "Alpha" in capsys.readouterr().out


def test_search_json_is_parseable(wired, capsys):
    assert main(["--json", "search", "Alpha"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["slug"] == "a"


def test_watch_add_defaults_to_canonical_rank(wired, capsys):
    assert main(["watch", "add", "Alpha"]) == 0
    assert wired.watchlist.get("a", 10) is not None


def test_watch_ls_lists_entries(wired, capsys):
    main(["watch", "add", "Alpha"])
    capsys.readouterr()
    main(["--json", "watch", "ls"])
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["slug"] == "a"


def test_group_flow(wired, capsys):
    assert main(["group", "new", "primes"]) == 0
    assert main(["group", "add", "primes", "Alpha"]) == 0
    assert main(["--json", "group", "show", "primes"]) == 0
    assert "a" in capsys.readouterr().out


def test_sync_dry_run_spends_nothing(wired, capsys):
    assert main(["--json", "sync", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["requests_spent"] == 0


def test_unknown_item_exits_nonzero_with_a_readable_message(wired, capsys):
    assert main(["watch", "add", "nonsense"]) == 1
    assert "nonsense" in capsys.readouterr().err
