from datetime import datetime, timezone

from wfm.analyzers.base import Context
from wfm.analyzers.runner import run_group, run_item
from wfm.features.types import FeatureSet, PriceFeatures, Provenance
from wfm.models import Direction, Horizon, Scope, Signal

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


class _Fake:
    def __init__(self, name, required, scope=Scope.ITEM, boom=False):
        self.name = name
        self.horizon = Horizon.DAILY
        self.scope = scope
        self.DEFAULTS = {}
        self._required = required
        self._boom = boom

    def required_features(self):
        return self._required

    def evaluate(self, fs, ctx):
        if self._boom:
            raise ValueError("analyzer exploded")
        slug = fs.slug if isinstance(fs, FeatureSet) else fs[0].slug
        return [
            Signal(slug=slug, rank=0, analyzer=self.name, ts=NOW, direction=Direction.BUY,
                   magnitude=1.0, confidence=0.5)
        ]


def _fs(available=("price",)) -> FeatureSet:
    return FeatureSet(
        slug="x", rank=0, ts=NOW, price=PriceFeatures(median_90d=40.0),
        provenance=Provenance(available=frozenset(available)),
    )


def test_runs_analyzers_whose_features_are_available():
    signals, skipped = run_item([_Fake("a", {"price"})], _fs(), Context(now=NOW))
    assert [s.analyzer for s in signals] == ["a"]
    assert skipped == []


def test_skips_rather_than_failing_on_missing_features():
    signals, skipped = run_item([_Fake("b", {"book"})], _fs(), Context(now=NOW))
    assert signals == []
    assert skipped == ["b"]


def test_one_broken_analyzer_does_not_stop_the_others():
    analyzers = [_Fake("boom", {"price"}, boom=True), _Fake("ok", {"price"})]
    signals, skipped = run_item(analyzers, _fs(), Context(now=NOW))
    assert [s.analyzer for s in signals] == ["ok"]
    assert "boom" in skipped


def test_item_scope_ignores_group_analyzers():
    signals, _ = run_item([_Fake("g", {"price"}, scope=Scope.GROUP)], _fs(), Context(now=NOW))
    assert signals == []


def test_group_scope_receives_the_whole_list():
    analyzer = _Fake("g", {"price"}, scope=Scope.GROUP)
    signals, skipped = run_group([analyzer], [_fs(), _fs()], Context(now=NOW))
    assert [s.analyzer for s in signals] == ["g"]
    assert skipped == []


def test_group_run_skips_when_any_member_lacks_the_features():
    analyzer = _Fake("g", {"book"}, scope=Scope.GROUP)
    signals, skipped = run_group([analyzer], [_fs(), _fs()], Context(now=NOW))
    assert signals == []
    assert skipped == ["g"]


def test_run_group_ignores_item_analyzers():
    signals, skipped = run_group([_Fake("i", {"price"}, scope=Scope.ITEM)], [_fs()], Context(now=NOW))
    assert signals == [] and skipped == []
