from pathlib import Path

import pytest

from wfm.config import MAX_REQUESTS_PER_SECOND, Config


def test_defaults():
    cfg = Config()
    assert cfg.requests_per_second == 2.8
    assert cfg.concurrency == 1
    assert cfg.platform == "pc"
    assert cfg.crossplay is True
    assert cfg.discord_webhook_url is None


def test_rate_is_clamped_to_the_published_ceiling():
    assert Config(requests_per_second=50.0).requests_per_second == MAX_REQUESTS_PER_SECOND
    assert MAX_REQUESTS_PER_SECOND == 3.0


def test_rate_may_be_lowered():
    assert Config(requests_per_second=0.5).requests_per_second == 0.5


def test_concurrency_cannot_be_raised():
    assert Config(concurrency=8).concurrency == 1


def test_user_agent_matches_the_documented_shape():
    ua = Config().user_agent
    assert ua.startswith("WFMStalker/")
    assert "(+http" in ua
    for banned in ("Mozilla", "Chrome", "Safari"):
        assert banned not in ua


def test_load_reads_toml_and_ignores_unknown_keys(tmp_path: Path):
    cfg_file = tmp_path / "wfm.toml"
    cfg_file.write_text(
        "requests_per_second = 1.5\n"
        "sweep_hour = 3\n"
        'discord_webhook_url = "https://example.invalid/hook"\n'
        "not_a_real_key = 7\n",
        encoding="utf-8",
    )
    cfg = Config.load(cfg_file)
    assert cfg.requests_per_second == 1.5
    assert cfg.sweep_hour == 3
    assert cfg.discord_webhook_url == "https://example.invalid/hook"


def test_load_env_overrides_file(tmp_path: Path, monkeypatch):
    cfg_file = tmp_path / "wfm.toml"
    cfg_file.write_text("requests_per_second = 1.5\n", encoding="utf-8")
    monkeypatch.setenv("WFM_REQUESTS_PER_SECOND", "0.9")
    monkeypatch.setenv("WFM_DB_PATH", str(tmp_path / "custom.db"))
    cfg = Config.load(cfg_file)
    assert cfg.requests_per_second == 0.9
    assert cfg.db_path == tmp_path / "custom.db"


def test_load_without_a_file_returns_defaults(tmp_path: Path):
    assert Config.load(tmp_path / "absent.toml").requests_per_second == 2.8


def test_env_rate_is_clamped_to_the_published_ceiling(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WFM_REQUESTS_PER_SECOND", "99")
    cfg = Config.load(tmp_path / "absent.toml")
    assert cfg.requests_per_second == MAX_REQUESTS_PER_SECOND


def test_non_positive_rate_is_rejected():
    for bad in (0, -5.0):
        with pytest.raises(ValueError):
            Config(requests_per_second=bad)


def test_env_override_weight_float_as_float(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WFM_W_VOL", "1.5")
    cfg = Config.load(tmp_path / "absent.toml")
    assert cfg.w_vol == 1.5
