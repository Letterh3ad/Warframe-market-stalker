from datetime import datetime, timezone

import pytest

from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.models import DailyCandle, Item
from wfm.services.context import AppContext
from wfm.validation.harness import replay, sweep_thresholds

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _dates(n: int) -> list[str]:
    # n consecutive days from 2026-04-01
    from datetime import date, timedelta

    base = date(2026, 4, 1)
    return [(base + timedelta(days=i)).isoformat() for i in range(n)]


def _series(slug: str, closes: list[float]) -> list[DailyCandle]:
    return [
        DailyCandle(slug=slug, rank=0, date=d, close=c, high=c + 1, low=c - 1, median=c, volume=25)
        for d, c in zip(_dates(len(closes)), closes)
    ]


@pytest.fixture
def ctx(conn):
    context = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    context.items.upsert_many(
        [
            Item(slug="dipper", name="Dipper", url_name="dipper", tags=("mod",)),
            Item(slug="steady", name="Steady", url_name="steady", tags=("mod",)),
        ]
    )
    # A deterministic sawtooth (median 50, MAD 2 so robust_z is defined) with a sharp
    # 3-day drop to 30 on indices 100-102, then recovery.
    dip = ([48.0, 52.0] * 50) + [30.0, 30.0, 30.0] + ([48.0, 52.0] * 14)
    context.daily.upsert_many(_series("dipper", dip))
    context.daily.upsert_many(_series("steady", [50.0] * 130))
    return context


def test_replay_finds_the_dip_and_scores_it_a_hit(ctx):
    result = replay(ctx, "revert", start="2026-07-08", end="2026-07-25", horizon_days=7,
                    slugs=["dipper"], threshold_overrides={"z_threshold": 1.0, "min_excess_return": 0.0})
    assert result.signals > 0
    assert result.hit_rate == pytest.approx(1.0)
    assert result.median_forward_return > 0


def test_replay_of_a_flat_series_emits_nothing(ctx):
    result = replay(ctx, "revert", start="2026-07-08", end="2026-07-25", slugs=["steady"])
    assert result.signals == 0
    assert result.hit_rate is None


def test_replay_never_looks_ahead(ctx):
    # The dip candles fall on 2026-07-10..12. Forward-return scoring now loads candles
    # past `end`, so those candles ARE in the target series during a replay that ends
    # 2026-07-09; only the per-day `c.date <= as_of` slice keeps the analyzer from seeing
    # them. Delete that slice and `before` starts firing.
    overrides = {"z_threshold": 1.0, "min_excess_return": 0.0}
    before = replay(ctx, "revert", start="2026-06-25", end="2026-07-09", horizon_days=7,
                    slugs=["dipper"], threshold_overrides=overrides)
    assert before.signals == 0, "the dip is loaded for scoring but must not be analysed early"
    through = replay(ctx, "revert", start="2026-06-25", end="2026-07-16", horizon_days=7,
                     slugs=["dipper"], threshold_overrides=overrides)
    assert through.signals > 0, "the same window run through the dip does fire"


def test_signals_within_the_horizon_of_end_are_still_scored(ctx):
    # The dip (2026-07-10..12) sits entirely inside horizon_days of `end`. Before the
    # end-boundary fix every forward return for these days fell off the loaded series and
    # the signals were dropped from the scored count.
    result = replay(ctx, "revert", start="2026-07-08", end="2026-07-12", horizon_days=7,
                    slugs=["dipper"], threshold_overrides={"z_threshold": 1.0, "min_excess_return": 0.0})
    assert result.signals > 0


def test_replay_rejects_a_non_item_analyzer(ctx, monkeypatch):
    from wfm.models import Scope

    class FakeGroup:
        name = "fake_group"
        scope = Scope.GROUP

    monkeypatch.setattr("wfm.validation.harness.registry.get", lambda name: FakeGroup())
    with pytest.raises(ValueError, match="ITEM scope"):
        replay(ctx, "fake_group", start="2026-07-08", end="2026-07-25")


def test_results_break_down_by_direction(ctx):
    result = replay(ctx, "revert", start="2026-07-08", end="2026-07-25", slugs=["dipper"],
                    threshold_overrides={"z_threshold": 1.0, "min_excess_return": 0.0})
    assert set(result.by_direction) <= {"buy", "sell"}
    assert sum(v["signals"] for v in result.by_direction.values()) == result.signals


def test_sweep_thresholds_reports_one_result_per_value(ctx):
    swept = sweep_thresholds(
        ctx, "revert", key="z_threshold", values=[1.0, 1.5, 5.0],
        start="2026-07-08", end="2026-07-25", slugs=["dipper"],
    )
    assert [value for value, _ in swept] == [1.0, 1.5, 5.0]
    assert swept[0][1].signals >= swept[-1][1].signals


def test_an_unknown_analyzer_raises(ctx):
    with pytest.raises(KeyError):
        replay(ctx, "nope", start="2026-07-08", end="2026-07-25")
