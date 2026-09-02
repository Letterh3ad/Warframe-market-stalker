import pytest

from wfm.analyzers import registry
from wfm.config import Config
from wfm.models import Horizon, Scope


def test_the_three_shipped_analyzers_are_registered():
    assert {a.name for a in registry.all()} == {"flip", "revert", "selltime"}


def test_lookup_by_name():
    assert registry.get("flip").horizon is Horizon.URGENT
    assert registry.get("revert").horizon is Horizon.DAILY


def test_every_shipped_analyzer_is_item_scoped_but_the_registry_admits_group():
    assert all(a.scope is Scope.ITEM for a in registry.all())
    assert Scope.GROUP in set(Scope)


def test_unknown_name_raises():
    with pytest.raises(KeyError):
        registry.get("nope")


def test_config_can_disable_an_analyzer():
    cfg = Config(analyzers={"flip": {"enabled": False}})
    assert {a.name for a in registry.enabled(cfg)} == {"revert", "selltime"}


def test_analyzers_are_enabled_by_default():
    assert len(registry.enabled(Config())) == 3


@pytest.mark.xfail(reason="real DEFAULTS arrive in tasks 3-4", strict=True)
def test_thresholds_merge_config_over_defaults():
    cfg = Config(analyzers={"flip": {"min_margin_plat": 25}})
    merged = registry.thresholds(cfg)
    assert merged["flip"]["min_margin_plat"] == 25
    assert "min_margin_pct" in merged["flip"], "unset keys keep their default"


def test_thresholds_drop_the_enabled_flag():
    cfg = Config(analyzers={"revert": {"enabled": False, "z_threshold": 9}})
    assert "enabled" not in registry.thresholds(cfg)["revert"]
    assert registry.thresholds(cfg)["revert"]["z_threshold"] == 9
