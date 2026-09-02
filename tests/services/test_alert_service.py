from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.alerts.base import DeliveryResult
from wfm.config import Config
from wfm.models import Direction, Horizon, Item, Signal
from wfm.services import alert_service
from wfm.services.context import AppContext

NOW = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)


class RecordingSink:
    def __init__(self, name="discord", fail=False):
        self.name = name
        self.batches: list[list[Signal]] = []
        self.texts: list[str] = []
        self._fail = fail

    async def deliver(self, signals):
        self.batches.append(list(signals))
        ids = [s.id for s in signals if s.id is not None]
        if self._fail:
            return DeliveryResult(sink=self.name, failed=ids, error="boom")
        return DeliveryResult(sink=self.name, delivered=ids)

    async def deliver_text(self, text):
        self.texts.append(text)
        if self._fail:
            return DeliveryResult(sink=self.name, error="boom")
        return DeliveryResult(sink=self.name)

    async def aclose(self):
        return None


@pytest.fixture
def ctx(conn):
    context = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    context.items.upsert_many([Item(slug="x", name="Ex Item", url_name="x")])
    return context


def _store(ctx, horizon=Horizon.DAILY, analyzer="revert", magnitude=2.0) -> Signal:
    signal = Signal(slug="x", rank=0, analyzer=analyzer, ts=ctx.clock.utcnow(),
                    direction=Direction.BUY, magnitude=magnitude, confidence=0.9,
                    horizon=horizon, evidence={"robust_z": -2.0})
    ctx.signals.insert(signal)
    return ctx.signals.query()[0]


async def test_deliver_marks_only_what_a_sink_confirmed(ctx):
    stored = _store(ctx, horizon=Horizon.URGENT, analyzer="flip", magnitude=20.0)
    sink = RecordingSink(name="terminal")
    results = await alert_service.deliver(ctx, [stored], sinks={"terminal": sink})
    assert results[0].delivered == [stored.id]
    assert ctx.signals.query()[0].alerted_at is not None


async def test_deliver_leaves_a_plain_daily_signal_for_the_digest(ctx):
    stored = _store(ctx, horizon=Horizon.DAILY, analyzer="revert")
    sink = RecordingSink(name="terminal")
    await alert_service.deliver(ctx, [stored], sinks={"terminal": sink})
    assert sink.batches == [[]]
    assert ctx.signals.query()[0].alerted_at is None


async def test_deliver_sends_a_daily_signal_live_when_the_item_is_overridden(ctx):
    ctx.watchlist.add("x", 0, added_at=ctx.clock.utcnow(), alert_override=True)
    stored = _store(ctx, horizon=Horizon.DAILY, analyzer="revert")
    sink = RecordingSink(name="terminal")
    await alert_service.deliver(ctx, [stored], sinks={"terminal": sink})
    assert sink.batches == [[stored]]
    assert ctx.signals.query()[0].alerted_at is not None


async def test_a_failed_sink_leaves_the_signal_undelivered(ctx):
    stored = _store(ctx, horizon=Horizon.URGENT)
    sink = RecordingSink(name="terminal", fail=True)
    await alert_service.deliver(ctx, [stored], sinks={"terminal": sink})
    assert ctx.signals.query()[0].alerted_at is None


async def test_the_digest_drains_undelivered_daily_signals(ctx):
    _store(ctx)
    _store(ctx, analyzer="selltime", magnitude=1.0)
    sink = RecordingSink()
    result = await alert_service.run_digest(ctx, sinks={"discord": sink})
    assert result["delivered"] == 2
    assert "2 signals" in sink.texts[0]
    assert all(s.alerted_at is not None for s in ctx.signals.query())


async def test_a_second_digest_run_sends_nothing(ctx):
    _store(ctx)
    sink = RecordingSink()
    await alert_service.run_digest(ctx, sinks={"discord": sink})
    second = await alert_service.run_digest(ctx, sinks={"discord": sink})
    assert second["delivered"] == 0
    assert len(sink.texts) == 1


async def test_a_crash_mid_digest_neither_double_sends_nor_drops(ctx):
    _store(ctx)
    failing = RecordingSink(fail=True)
    first = await alert_service.run_digest(ctx, sinks={"discord": failing})
    assert first["delivered"] == 0
    assert ctx.signals.query()[0].alerted_at is None

    working = RecordingSink()
    second = await alert_service.run_digest(ctx, sinks={"discord": working})
    assert second["delivered"] == 1
    assert len(working.texts) == 1


async def test_urgent_signals_are_not_swept_into_the_digest(ctx):
    _store(ctx, horizon=Horizon.URGENT, analyzer="flip")
    sink = RecordingSink()
    assert (await alert_service.run_digest(ctx, sinks={"discord": sink}))["delivered"] == 0


async def test_without_a_webhook_the_digest_still_marks_terminal_delivery(ctx, capsys):
    _store(ctx)
    result = await alert_service.run_digest(ctx)
    assert result["delivered"] == 1
    assert result["sinks"] == ["terminal"]


async def test_the_digest_renders_every_pending_signal_not_just_a_capped_top_slice(ctx):
    for i in range(20):
        _store(ctx, magnitude=float(i))
    sink = RecordingSink()
    result = await alert_service.run_digest(ctx, sinks={"discord": sink})
    assert result["delivered"] == 20
    assert "more below the cap" not in sink.texts[0]
    assert all(s.alerted_at is not None for s in ctx.signals.query(limit=50))


async def test_a_broken_discord_does_not_block_the_terminal_digest_from_marking(ctx):
    _store(ctx)
    sinks = {"terminal": RecordingSink(name="terminal"), "discord": RecordingSink(fail=True)}
    result = await alert_service.run_digest(ctx, sinks=sinks)
    assert result["delivered"] == 1
    assert "discord" in result["error"]
    assert ctx.signals.query()[0].alerted_at is not None

    second = await alert_service.run_digest(ctx, sinks=sinks)
    assert second["delivered"] == 0


def test_list_signals_renders_through_the_same_formatter(ctx):
    _store(ctx)
    text = alert_service.render_signals(ctx)
    assert "Ex Item" in text
    assert "revert" in text


def test_list_signals_returns_plain_dicts_for_json(ctx):
    _store(ctx)
    rows = alert_service.list_signals(ctx)
    assert rows[0]["analyzer"] == "revert"
    assert rows[0]["evidence"]["robust_z"] == -2.0
