from __future__ import annotations

from fastapi import Request

from wfm.features.market import MarketContext
from wfm.services import feature_service

MARKET_TTL_S = 900.0
"""The market block is a catalog-wide sampled figure that moves slowly, and rebuilding
it per request means one full sampled pass per click while browsing. report_group
already builds it once for a whole group for the same reason.
"""


async def get_market(request: Request) -> MarketContext:
    ctx = request.app.state.ctx
    now = ctx.clock.utcnow()
    cached = getattr(request.app.state, "market_cache", None)
    if cached is not None:
        built_at, market = cached
        if (now - built_at).total_seconds() < MARKET_TTL_S:
            return market
    market = feature_service.market_context(ctx, now=now)
    request.app.state.market_cache = (now, market)
    return market
