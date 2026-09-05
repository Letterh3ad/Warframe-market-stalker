from __future__ import annotations

from fastapi import FastAPI

from wfm.gui.errors import install_error_handlers
from wfm.services.context import AppContext


def build_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="Warframe Market Stalker")
    app.state.ctx = ctx
    install_error_handlers(app)
    return app
