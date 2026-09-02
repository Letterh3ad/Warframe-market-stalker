import json
from datetime import datetime, timezone

import httpx

from wfm.alerts.discord import DiscordSink
from wfm.models import Direction, Horizon, Signal

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
WEBHOOK = "https://discord.com/api/webhooks/1/token"


def _signal(signal_id=1) -> Signal:
    return Signal(
        id=signal_id, slug="x", rank=0, analyzer="flip", ts=NOW, direction=Direction.BUY,
        magnitude=20.0, confidence=0.8, horizon=Horizon.URGENT, evidence={"fair_value": 50.0},
    )


def _sink(handler, **kwargs) -> DiscordSink:
    return DiscordSink(WEBHOOK, transport=httpx.MockTransport(handler), **kwargs)


async def test_posts_one_message_containing_every_signal():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(204)

    sink = _sink(handler)
    result = await sink.deliver([_signal(1), _signal(2)])
    await sink.aclose()
    assert len(seen) == 1
    assert seen[0]["content"].count("flip") == 2
    assert result.delivered == [1, 2]


async def test_it_posts_only_to_the_configured_webhook():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(204)

    sink = _sink(handler)
    await sink.deliver([_signal()])
    await sink.aclose()
    assert seen == [WEBHOOK]


async def test_a_failed_delivery_is_reported_not_raised():
    sink = _sink(lambda r: httpx.Response(500, text="nope"))
    result = await sink.deliver([_signal()])
    await sink.aclose()
    assert result.delivered == []
    assert result.failed == [1]
    assert "500" in result.error


async def test_a_transport_error_is_swallowed():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    sink = _sink(handler)
    result = await sink.deliver([_signal()])
    await sink.aclose()
    assert result.failed == [1]
    assert result.error is not None


async def test_a_long_message_is_split_under_the_two_thousand_character_limit():
    posted = []

    def handler(request):
        posted.append(json.loads(request.content)["content"])
        return httpx.Response(204)

    sink = _sink(handler)
    await sink.deliver([_signal(i) for i in range(1, 60)])
    await sink.aclose()
    assert len(posted) > 1
    assert all(len(chunk) <= 2000 for chunk in posted)


async def test_deliver_text_posts_the_digest_verbatim():
    posted = []

    def handler(request):
        posted.append(json.loads(request.content)["content"])
        return httpx.Response(204)

    sink = _sink(handler)
    await sink.deliver_text("Daily digest: 3 signals")
    await sink.aclose()
    assert posted == ["Daily digest: 3 signals"]


async def test_delivering_nothing_makes_no_request():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(204)

    sink = _sink(handler)
    assert (await sink.deliver([])).delivered == []
    await sink.aclose()
    assert calls == []
