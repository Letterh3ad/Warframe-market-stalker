"""Capture live payloads into tests/fixtures for manual inspection.

Run by hand: python scripts/capture_fixtures.py primed_continuity
Costs four requests. Never invoked by the test suite.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from wfm.api.breaker import CircuitBreaker
from wfm.api.client import WFMClient
from wfm.api.endpoints import items_url, orders_url, statistics_url, versions_url
from wfm.api.ratelimit import TokenBucket
from wfm.clock import SystemClock
from wfm.config import Config
from wfm.sync.budget import Budget

OUT = Path("tests/fixtures")


async def main(slug: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    config = Config()
    clock = SystemClock()
    budget = Budget(TokenBucket(config.requests_per_second, clock), clock)
    async with WFMClient(config, budget, CircuitBreaker(clock=clock), clock) as client:
        for name, url in (
            ("versions", versions_url()),
            ("items", items_url()),
            (f"orders_{slug}", orders_url(slug)),
            (f"statistics_{slug}", statistics_url(slug)),
        ):
            payload = await client.get_json(url)
            (OUT / f"{name}.json").write_text(
                json.dumps(payload, indent=2)[:200_000], encoding="utf-8"
            )
            print(f"wrote {name}.json")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "primed_continuity"))
