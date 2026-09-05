from datetime import datetime, timezone

from wfm.config import Config
from wfm.gui.app import build_app
from wfm.services.context import AppContext


def test_build_app_exposes_the_context_on_app_state(conn):
    ctx = AppContext(Config(), conn=conn)
    app = build_app(ctx)
    assert app.state.ctx is ctx
