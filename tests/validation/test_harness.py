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


def _series(slug: str, closes: list[float], rank: int = 0) -> list[DailyCandle]:
    return [
        DailyCandle(slug=slug, rank=rank, date=d, close=c, high=c + 1, low=c - 1, median=c, volume=25)
        for d, c in zip(_dates(len(closes)), closes)
    ]


# Shared shape: a deterministic sawtooth (median 50, MAD 2 so robust_z is defined) with a
# sharp 3-day drop to 30 on indices 100-102, then recovery.
_DIP = ([48.0, 52.0] * 50) + [30.0, 30.0, 30.0] + ([48.0, 52.0] * 14)
_FLAT = [50.0] * 130


@pytest.fixture
def ctx(conn):
    context = AppContext(Config(), conn=conn, clock=FakeClock(start_utc=NOW))
    context.items.upsert_many(
        [
            Item(slug="dipper", name="Dipper", url_name="dipper", tags=("mod",)),
            Item(slug="steady", name="Steady", url_name="steady", tags=("mod",)),
        ]
    )
    context.daily.upsert_many(_series("dipper", _DIP))
    context.daily.upsert_many(_series("steady", _FLAT))
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


def test_replay_isolates_a_failing_item_and_reports_it(ctx, monkeypatch):
    # A second dip series so a real signal is still available to score once "dipper"
    # (the flaky one) is skipped.
    ctx.items.upsert_many(
        [Item(slug="dipper2", name="Dipper2", url_name="dipper2", tags=("mod",))]
    )
    ctx.daily.upsert_many(_series("dipper2", _DIP))

    from wfm.analyzers import registry

    real = registry.get("revert")

    class FlakyAnalyzer:
        name = real.name
        scope = real.scope
        DEFAULTS = real.DEFAULTS

        def required_features(self):
            return real.required_features()

        def evaluate(self, fs, c):
            if fs.slug == "dipper":
                raise RuntimeError("boom")
            return real.evaluate(fs, c)

    monkeypatch.setattr("wfm.validation.harness.registry.get", lambda name: FlakyAnalyzer())

    result = replay(
        ctx, "revert", start="2026-07-08", end="2026-07-25", horizon_days=7,
        slugs=["dipper", "dipper2"],
        threshold_overrides={"z_threshold": 1.0, "min_excess_return": 0.0},
    )

    assert result.signals > 0, "dipper2 must still be scored despite dipper failing"
    assert result.failures > 0
    assert "dipper" in result.failed_slugs


def test_replay_reads_the_items_canonical_rank(ctx):
    # Rank 0 is flat (no signal); the dip only exists at rank 5. Production reads
    # item.canonical_rank, so the harness must too or it silently tunes against the
    # wrong series for a ranked mod.
    ctx.items.upsert_many(
        [Item(slug="ranked", name="Ranked", url_name="ranked", tags=("mod",), canonical_rank=5)]
    )
    ctx.daily.upsert_many(_series("ranked", _FLAT, rank=0))
    ctx.daily.upsert_many(_series("ranked", _DIP, rank=5))

    result = replay(
        ctx, "revert", start="2026-07-08", end="2026-07-25", horizon_days=7,
        slugs=["ranked"],
        threshold_overrides={"z_threshold": 1.0, "min_excess_return": 0.0},
    )

    assert result.signals > 0
