import inspect
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from tests.fakes.clock import FakeClock
from wfm.api.breaker import CircuitBreaker
from wfm.api.client import WFMClient
from wfm.api.ratelimit import TokenBucket
from wfm.config import MAX_REQUESTS_PER_SECOND, Config
from wfm.sync.budget import Budget

START = datetime(2026, 8, 27, tzinfo=timezone.utc)
SOURCE_ROOT = Path(__file__).resolve().parents[2] / "wfm"


@pytest.mark.parametrize("requested", [3.1, 10.0, 1000.0])
def test_no_config_path_can_exceed_the_published_ceiling(requested):
    cfg = Config(requests_per_second=requested)
    assert cfg.requests_per_second <= MAX_REQUESTS_PER_SECOND
    bucket = TokenBucket(cfg.requests_per_second, FakeClock(start_utc=START))
    assert bucket.rate_per_second <= MAX_REQUESTS_PER_SECOND


def test_env_override_cannot_exceed_the_ceiling(monkeypatch, tmp_path):
    monkeypatch.setenv("WFM_REQUESTS_PER_SECOND", "99")
    assert Config.load(tmp_path / "absent.toml").requests_per_second == MAX_REQUESTS_PER_SECOND


def test_user_agent_is_descriptive_and_not_a_browser():
    ua = Config().user_agent
    assert ua.startswith("WFMStalker/")
    assert "(+http" in ua
    for banned in ("Mozilla", "AppleWebKit", "Chrome", "Safari", "Edge"):
        assert banned not in ua


async def test_every_request_carries_the_user_agent():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent"))
        return httpx.Response(200, json={"data": {}})

    clock = FakeClock(start_utc=START)
    config = Config()
    client = WFMClient(
        config,
        Budget(TokenBucket(config.requests_per_second, clock), clock),
        CircuitBreaker(clock=clock),
        clock,
        transport=httpx.MockTransport(handler),
    )
    await client.get_json("https://api.warframe.market/v2/versions")
    await client.aclose()
    assert seen == [config.user_agent]


# The Discord sink is the one module allowed its own transport and its own POST. It
# never touches warframe.market; test_the_discord_sink_* below pins that down.
DISCORD_SINK = SOURCE_ROOT / "alerts" / "discord.py"


def test_only_the_client_and_discord_sink_construct_an_http_transport():
    offenders = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if path.name == "client.py" or path == DISCORD_SINK:
            continue
        text = path.read_text(encoding="utf-8")
        if "httpx.AsyncClient(" in text or "requests." in text:
            offenders.append(str(path))
    assert offenders == []


def test_no_write_verb_reaches_the_transport_except_in_the_discord_sink():
    # Matched against the transport rather than the bare verb, so that a cache put or
    # a dict pop cannot be mistaken for an HTTP write. Narrowed, not deleted: the
    # Discord sink is exempt and pinned down separately.
    pattern = re.compile(r"(?:_http|httpx|requests|session|client)\.(?:post|put|patch|delete)\(")
    offenders = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if path == DISCORD_SINK:
            continue
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            offenders.append(f"{path}: {match.group(0)}")
    assert offenders == [], "read only forever: no write verb may reach the transport"


def test_the_discord_sink_posts_only_to_its_configured_webhook():
    text = DISCORD_SINK.read_text(encoding="utf-8")
    assert "warframe.market" not in text
    assert text.count("._http.post(") == 1
    assert "self._http.post(self._url" in text


def test_client_has_no_method_that_issues_a_write():
    method_names = {name for name, _ in inspect.getmembers(WFMClient, inspect.isfunction)}
    assert method_names & {"post", "put", "patch", "delete"} == set()
