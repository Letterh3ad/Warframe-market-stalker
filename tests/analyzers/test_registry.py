import pytest

import wfm.analyzers
from wfm.analyzers import registry
from wfm.config import Config
from wfm.models import Horizon, Scope


def test_the_three_shipped_analyzers_are_registered():
    assert {a.name for a in registry.all()} == {"flip", "revert", "selltime", "set_arbitrage"}


def test_a_new_analyzer_module_is_discovered_without_editing_the_registry(tmp_path, monkeypatch):
    # The contract behind drop-in modules: a file in the wfm.analyzers package path
    # that exposes ANALYZER is picked up by discover(), no import list to maintain.
    # The probe lives in a tmp dir grafted onto __path__, never in the tracked tree.
    monkeypatch.setattr(
        wfm.analyzers, "__path__", [*wfm.analyzers.__path__, str(tmp_path)]
    )
    (tmp_path / "probe_analyzer.py").write_text(
        "from wfm.models import Horizon, Scope\n"
        "class _Probe:\n"
        "    name = 'probe'\n"
        "    scope = Scope.ITEM\n"
        "    horizon = Horizon.DAILY\n"
        "    DEFAULTS = {}\n"
        "    def evaluate(self, fs, ctx):\n"
        "        return []\n"
        "ANALYZER = _Probe()\n",
        encoding="utf-8",
    )
    try:
        registry.discover()
        assert "probe" in {a.name for a in registry.all()}
    finally:
        registry._REGISTERED.pop("probe", None)


def test_discover_skips_a_module_that_fails_to_import(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(
        wfm.analyzers, "__path__", [*wfm.analyzers.__path__, str(tmp_path)]
    )
    (tmp_path / "broken_analyzer.py").write_text("import a_package_that_is_not_installed\n")
    registry.discover()  # must not raise
    assert {"flip", "revert", "selltime", "set_arbitrage"} <= {a.name for a in registry.all()}


def test_lookup_by_name():
    assert registry.get("flip").horizon is Horizon.URGENT
    assert registry.get("revert").horizon is Horizon.DAILY


def test_every_shipped_analyzer_is_item_scoped_but_the_registry_admits_group():
    item_scoped = {a.name for a in registry.all() if a.scope is Scope.ITEM}
    assert item_scoped == {"flip", "revert", "selltime"}
    group_scoped = {a.name for a in registry.all() if a.scope is Scope.GROUP}
    assert group_scoped == {"set_arbitrage"}
    assert Scope.GROUP in set(Scope)


def test_unknown_name_raises():
    with pytest.raises(KeyError):
        registry.get("nope")


def test_config_can_disable_an_analyzer():
    cfg = Config(analyzers={"flip": {"enabled": False}})
    assert {a.name for a in registry.enabled(cfg)} == {"revert", "selltime", "set_arbitrage"}


def test_analyzers_are_enabled_by_default():
    assert len(registry.enabled(Config())) == 4


def test_thresholds_merge_config_over_defaults():
    cfg = Config(analyzers={"flip": {"min_margin_plat": 25}})
    merged = registry.thresholds(cfg)
    assert merged["flip"]["min_margin_plat"] == 25
    assert "min_margin_pct" in merged["flip"], "unset keys keep their default"


def test_thresholds_drop_the_enabled_flag():
    cfg = Config(analyzers={"revert": {"enabled": False, "z_threshold": 9}})
    assert "enabled" not in registry.thresholds(cfg)["revert"]
    assert registry.thresholds(cfg)["revert"]["z_threshold"] == 9
