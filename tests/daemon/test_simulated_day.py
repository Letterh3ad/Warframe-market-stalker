from datetime import datetime, timezone

import pytest

from tests.fakes.api import StubClient
from tests.fakes.clock import FakeClock
from wfm.config import Config
from wfm.daemon.runner import Daemon
from wfm.models import DailyCandle, Item
from wfm.services.context import AppContext

# 05:00, not 10:00: sweep_hour=4 and digest_hour=9 would otherwise both already be
# "due" on iteration 1 (see test_runner.py's fixture). The fixture marks Aug 27's
# sweep and digest done so neither fires at START, but the simulated day below
# deliberately runs long enough to cross midnight: sweep_hour and digest_hour come
# due again on Aug 28, and the test asserts each fires exactly once over the run.
START = datetime(2026, 8, 27, 5, 0, tzinfo=timezone.utc)

VERSIONS = {"collections": {"items": "v42"}}
STATS = {"payload": {"statistics_closed": {"90days": [], "48hours": []}}}


def _orders(ask: int) -> list[dict]:
    return [
        {"platinum": ask, "quantity": 2, "rank": 0, "type": "sell", "visible": True,
         "user": {"status": "ingame"}, "updatedAt": "2026-08-27T04:00:00Z"},
        {"platinum": ask - 5, "quantity": 3, "rank": 0, "type": "buy", "visible": True,
         "user": {"status": "online"}, "updatedAt": "2026-08-27T04:00:00Z"},
    ]


class MovingBookClient(StubClient):
    """The hot item's book changes on every poll, the cold item's never does.

    Records a wall-clock timestamp alongside each call: gap assertions need real
    timing, not just call order, and StubClient's plain call list has none.
    """

    def __init__(self, clock):
        super().__init__({"/versions": VERSIONS, "/items": [], "/statistics": STATS})
        self._tick = 0
        self._clock = clock
        self.timed_calls: list[tuple[str, datetime]] = []

    async def get_json(self, url, params=None, priority=None, use_cache=False):
        self.calls.append((url, priority))
        self.timed_calls.append((url, self._clock.utcnow()))
        if "/orders/item/hot" in url:
            self._tick += 1
            return _orders(40 + self._tick % 7)
        if "/orders/item/cold" in url:
            return _orders(40)
        return self.payload_for(url)


@pytest.fixture
def ctx(conn):
    clock = FakeClock(start_utc=START)
    client = MovingBookClient(clock)
    context = AppContext(Config(), conn=conn, clock=clock, client=client)
    for slug, volume in (("hot", 400), ("cold", 6)):
        context.items.upsert_many([Item(slug=slug, name=slug, url_name=slug, tags=("mod",))])
        # hot's wide high/low range (vs. cold's narrow one) pushes its ATR-based
        # volatility comfortably past score_saturation, so its earned interval
        # actually clamps to poll_ceiling_minutes rather than just approaching it
        # (finding 2 of the review): the ceiling clamp needs a score that would
        # overshoot it to be under real test, not one that happens to land under it.
        high, low = (90, 10) if slug == "hot" else (60, 40)
        context.daily.upsert_many(
            [DailyCandle(slug=slug, rank=0, date=f"2026-06-{d:02d}",
                         close=50 + (d % 9 if slug == "hot" else 0), high=high, low=low,
                         median=50, volume=volume) for d in range(1, 31)]
        )
        context.watchlist.add(slug, 0, START)
    context.daemon_state.mark_started(pid=1, when=START)
    context.daemon_state.mark_daily_done("sweep", START.date())
    context.daemon_state.mark_daily_done("digest", START.date())
    return context


def _slug_of(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def _gaps_minutes(timed_calls: list[tuple[str, datetime]], slug: str) -> list[float]:
    times = [ts for url, ts in timed_calls
             if "/orders/item/" in url and _slug_of(url) == slug]
    times.sort()
    return [(b - a).total_seconds() / 60 for a, b in zip(times, times[1:])]


async def test_a_simulated_day_respects_the_floor_the_ceiling_and_the_budget(ctx):
    # Long enough to cross midnight into Aug 28's sweep_hour (4) and digest_hour (9),
    # not just span a single day: see the report assertions below (review finding 1).
    report = await Daemon(ctx).run(max_iterations=2000)
    elapsed_hours = (ctx.clock.utcnow() - START).total_seconds() / 3600
    client = ctx.new_client()
    counts: dict[str, int] = {}
    for url, _ in client.calls:
        if "/orders/item/" in url:
            counts[_slug_of(url)] = counts.get(_slug_of(url), 0) + 1

    # The daily work is not incidental here: the run deliberately spans a day
    # boundary (see START's comment), and each must fire exactly once over it.
    # There is no daily-boundary clamp on the loop (a known deferred item), so
    # this only asserts each fires once, not the exact minute it does.
    assert report.sweeps == 1
    assert report.digests == 1

    # the volatile, liquid, moving item polls harder than the flat one. This is
    # also the assertion that catches a floor/ceiling swap inside PollQueue: the
    # gap bounds below are numerically the same [2, 30] range either way round, but
    # a swap makes the high-scoring item earn the long interval instead of the
    # short one, which flips this comparison.
    assert counts["hot"] > counts["cold"]
    # nothing polls faster than the 2 minute ceiling. hot's score is pushed past
    # score_saturation (see the fixture), so it polls at the ceiling for the whole
    # run and this bound is close to tight, not just a loose upper limit.
    assert counts["hot"] <= elapsed_hours * 30 + 1
    # nothing polls slower than the 30 minute floor while budget remains
    assert counts["cold"] >= elapsed_hours - 1

    ceiling = ctx.config.poll_ceiling_minutes
    floor = ctx.config.poll_floor_minutes
    for slug in ("hot", "cold"):
        gaps = _gaps_minutes(client.timed_calls, slug)
        assert gaps, f"expected repeated polls of {slug} over a simulated day"
        # No single item is polled twice inside the ceiling. hot's score sits well
        # past saturation, so this exercises interval_minutes's clamp for real: an
        # unclamped fraction would push hot's interval below the ceiling here.
        assert min(gaps) >= ceiling - 1e-6, f"{slug} polled faster than the ceiling allows"
        # No watched item is starved past the floor plus one interval of decay slack.
        assert max(gaps) <= floor * 2 + 1e-6, f"{slug} starved past the floor's slack"


async def test_a_dead_item_decays_out_of_the_hot_band(ctx):
    await Daemon(ctx).run(max_iterations=300)
    first_half = 0
    second_half = 0
    cold_calls = [i for i, (url, _) in enumerate(ctx.new_client().calls)
                  if "/orders/item/cold" in url]
    midpoint = len(ctx.new_client().calls) // 2
    for index in cold_calls:
        if index < midpoint:
            first_half += 1
        else:
            second_half += 1
    assert second_half <= first_half, "an unchanging book is polled no harder over time"
