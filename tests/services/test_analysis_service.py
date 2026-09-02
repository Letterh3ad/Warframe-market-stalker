from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.models import DailyCandle, Direction, Horizon, Item, Signal, Trade
from wfm.services import analysis_service
from wfm.services.context import AppContext

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def ctx(conn):
    context = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    context.items.upsert_many([Item(slug="x", name="X", url_name="x", tags=("mod",))])
    context.daily.upsert_many(
        [
            DailyCandle(slug="x", rank=0, date=f"2026-06-{d:02d}", close=50, high=52, low=48,
                        median=50, volume=30)
            for d in range(1, 31)
        ]
    )
    return context


def test_context_carries_holdings_from_the_ledger(ctx):
    ctx.trades.record(Trade(slug="x", rank=0, ts=NOW, side=Direction.BUY, quantity=3, platinum=40))
    analyzer_ctx = analysis_service.build_context(ctx)
    assert analyzer_ctx.holding_for("x", 0).quantity == 3
    assert analyzer_ctx.holding_for("x", 0).avg_cost == 40.0


def test_context_carries_merged_thresholds(ctx):
    thresholds = analysis_service.build_context(ctx).thresholds
    assert thresholds["flip"]["min_margin_plat"] == 10


def test_analyze_item_persists_signals_and_reports_skips(ctx):
    result = analysis_service.analyze_item(ctx, "x", 0)
    assert isinstance(result["signals"], list)
    assert "flip" in result["skipped"], "no book was fetched, so flip must be skipped"
    assert len(ctx.signals.query()) == len(result["signals"])


def test_a_still_open_signal_is_not_re_emitted(ctx, monkeypatch):
    def fake_run(analyzers, fs, analyzer_ctx):
        return (
            [
                Signal(slug="x", rank=0, analyzer="revert", ts=analyzer_ctx.now,
                       direction=Direction.BUY, magnitude=2.0, confidence=0.7,
                       horizon=Horizon.DAILY,
                       expires_at=analyzer_ctx.now + timedelta(hours=48),
                       evidence={"robust_z": -2.0})
            ],
            [],
        )

    monkeypatch.setattr("wfm.services.analysis_service.run_item", fake_run)
    first = analysis_service.analyze_item(ctx, "x", 0)
    assert len(first["signals"]) == 1
    second = analysis_service.analyze_item(ctx, "x", 0)
    assert second["signals"] == []
    assert second["suppressed"] == ["revert"]
    assert len(ctx.signals.query()) == 1


def test_the_cooldown_suppresses_a_repeat_after_a_signal_expires(ctx, monkeypatch):
    ctx.config = replace(ctx.config, cooldown_minutes=120)

    def fake_run(analyzers, fs, analyzer_ctx):
        return (
            [
                Signal(slug="x", rank=0, analyzer="revert", ts=analyzer_ctx.now,
                       direction=Direction.BUY, magnitude=2.0, confidence=0.7,
                       horizon=Horizon.DAILY,
                       expires_at=analyzer_ctx.now + timedelta(minutes=1),
                       evidence={})
            ],
            [],
        )

    monkeypatch.setattr("wfm.services.analysis_service.run_item", fake_run)
    analysis_service.analyze_item(ctx, "x", 0)
    ctx.clock.advance(60 * 30)
    assert analysis_service.analyze_item(ctx, "x", 0)["signals"] == []
    ctx.clock.advance(60 * 120)
    assert len(analysis_service.analyze_item(ctx, "x", 0)["signals"]) == 1


def test_persisted_evidence_is_json_serializable(ctx, monkeypatch):
    import json

    def fake_run(analyzers, fs, analyzer_ctx):
        return (
            [Signal(slug="x", rank=0, analyzer="revert", ts=analyzer_ctx.now,
                    direction=Direction.BUY, magnitude=1.0, confidence=0.5,
                    horizon=Horizon.DAILY, expires_at=analyzer_ctx.now + timedelta(hours=48),
                    evidence={"a": 1, "b": None, "c": 0.25})],
            [],
        )

    monkeypatch.setattr("wfm.services.analysis_service.run_item", fake_run)
    analysis_service.analyze_item(ctx, "x", 0)
    stored = ctx.signals.query()[0]
    assert json.loads(json.dumps(stored.evidence)) == stored.evidence


def test_analyze_group_covers_every_member(ctx):
    ctx.groups.create("mods", NOW)
    ctx.groups.add_member("mods", "x", 0)
    result = analysis_service.analyze_group(ctx, "mods")
    assert result["name"] == "mods"
    assert len(result["items"]) == 1
