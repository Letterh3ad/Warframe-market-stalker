from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

import wfm

MAX_REQUESTS_PER_SECOND = 3.0
"""Published public limit at docs.warframe.market. Never configurable above this."""

CONTACT_URL = "https://github.com/Faye/Warframe-market-stalker"

_ENV_PREFIX = "WFM_"


@dataclass(frozen=True)
class Config:
    db_path: Path = Path("wfm_market.db")
    pid_file: Path = Path("wfm.pid")
    requests_per_second: float = 2.8
    concurrency: int = 1
    request_timeout_s: float = 20.0
    platform: str = "pc"
    language: str = "en"
    crossplay: bool = True
    sweep_hour: int = 4
    digest_hour: int = 9
    poll_floor_minutes: int = 30
    poll_ceiling_minutes: int = 2
    interactive_per_minute: int = 30
    raw_sample_rate: int = 50
    discord_webhook_url: str | None = None
    discord_min_confidence: float = 0.6
    discord_min_magnitude: float = 0.0
    persist_features: bool = False
    cooldown_minutes: int = 120
    w_vol: float = 1.0
    w_liq: float = 0.5
    w_spread: float = 0.8
    w_pin: float = 1.5
    score_saturation: float = 1.0
    decay_after_unchanged_polls: int = 3
    catchup_max_items: int = 25
    analyzers: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Clamped at the top, rejected at the bottom: a rate of 0 never grants a token
        # and a negative one makes a 1/rate sleep negative, i.e. no limit at all.
        if self.requests_per_second <= 0:
            raise ValueError(
                f"requests_per_second must be positive, got {self.requests_per_second}"
            )
        if self.requests_per_second > MAX_REQUESTS_PER_SECOND:
            object.__setattr__(self, "requests_per_second", MAX_REQUESTS_PER_SECOND)
        if self.concurrency != 1:
            object.__setattr__(self, "concurrency", 1)
        object.__setattr__(self, "db_path", Path(self.db_path))
        object.__setattr__(self, "pid_file", Path(self.pid_file))

    @property
    def user_agent(self) -> str:
        return f"WFMStalker/{wfm.__version__} (+{CONTACT_URL})"

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = Path(path) if path is not None else Path("wfm.toml")
        values: dict = {}
        if path.exists():
            values = tomllib.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in values.items() if k in known})
        return replace(cfg, **cls._env_overrides(known))

    @staticmethod
    def _env_overrides(known: set[str]) -> dict:
        out: dict = {}
        for name in known:
            raw = os.environ.get(_ENV_PREFIX + name.upper())
            if raw is None:
                continue
            if name in ("db_path", "pid_file"):
                out[name] = Path(raw)
            elif name in (
                "requests_per_second",
                "request_timeout_s",
                "discord_min_confidence",
                "discord_min_magnitude",
                "w_vol",
                "w_liq",
                "w_spread",
                "w_pin",
                "score_saturation",
            ):
                out[name] = float(raw)
            elif name in ("crossplay", "persist_features"):
                out[name] = raw.strip().lower() in ("1", "true", "yes")
            elif name in ("platform", "language", "discord_webhook_url"):
                out[name] = raw
            else:
                out[name] = int(raw)
        return out
