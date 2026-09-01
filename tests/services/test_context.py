import sqlite3

import pytest

from wfm.config import Config
from wfm.services.context import AppContext


async def test_aclose_does_not_close_an_injected_connection(conn):
    ctx = AppContext(Config(), conn=conn)
    await ctx.aclose()
    conn.execute("SELECT 1")  # raises if aclose() closed it


async def test_aclose_closes_a_connection_it_opened_itself(tmp_path):
    ctx = AppContext(Config(db_path=tmp_path / "owned.db"))
    conn = ctx.conn
    await ctx.aclose()
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
