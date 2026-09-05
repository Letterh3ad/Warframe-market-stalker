from __future__ import annotations

from fastapi import FastAPI

from wfm.gui.errors import install_error_handlers
from wfm.gui.routes import groups, items, watchlist
from wfm.services.context import AppContext


def build_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="Warframe Market Stalker")
    app.state.ctx = ctx
    install_error_handlers(app)
    app.include_router(watchlist.router)
    app.include_router(items.router)
    app.include_router(groups.router)
    return app
