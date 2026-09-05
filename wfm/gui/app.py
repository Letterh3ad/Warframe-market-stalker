from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from wfm.gui.errors import install_error_handlers
from wfm.gui.routes import catalog, daemon, groups, items, ledger, signals, signals_ws, watchlist
from wfm.services.context import AppContext

STATIC_DIR = Path(__file__).parent / "static"


def build_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="Warframe Market Stalker")
    app.state.ctx = ctx
    install_error_handlers(app)
    app.include_router(catalog.router)
    app.include_router(watchlist.router)
    app.include_router(items.router)
    app.include_router(groups.router)
    app.include_router(daemon.router)
    app.include_router(ledger.router)
    app.include_router(signals.router)
    app.include_router(signals_ws.router)

    # Resolved from the package, not the working directory, so the dashboard works
    # regardless of where `wfm daemon start` was run from. Mounted under a prefix
    # rather than as a catch-all at "/", which would shadow the API routers.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
