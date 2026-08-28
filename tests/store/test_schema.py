import pytest

EXPECTED_TABLES = {
    "items",
    "daily_stats",
    "hourly_stats",
    "order_snapshots",
    "order_snapshots_raw",
    "signals",
    "trades",
    "watchlist",
    "groups",
    "group_members",
    "sweep_state",
    "features",
    "http_cache",
}


def test_all_tables_exist(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= names


def test_holdings_view_exists(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert "holdings" in names


@pytest.mark.parametrize(
    "table",
    ["daily_stats", "hourly_stats", "order_snapshots", "signals", "watchlist", "trades"],
)
def test_rank_is_not_null_and_defaults_to_zero(conn, table):
    cols = {r["name"]: r for r in conn.execute(f"PRAGMA table_info({table})")}
    assert cols["rank"]["notnull"] == 1
    assert cols["rank"]["dflt_value"] == "0"


@pytest.mark.parametrize(
    "table", ["daily_stats", "hourly_stats", "order_snapshots", "watchlist"]
)
def test_price_tables_key_on_slug_and_rank(conn, table):
    key = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})") if r["pk"]]
    assert key[:2] == ["slug", "rank"]


def test_trade_side_is_constrained(conn):
    with pytest.raises(Exception):
        conn.execute(
            'INSERT INTO trades(slug,"rank",ts,side,quantity,platinum) '
            "VALUES('x',0,'2026-08-27T00:00:00+00:00','gift',1,10)"
        )


def test_group_members_cascade_on_group_delete(conn):
    conn.execute("INSERT INTO groups(name,created_at) VALUES('primes','2026-08-27T00:00:00+00:00')")
    gid = conn.execute("SELECT id FROM groups").fetchone()[0]
    conn.execute('INSERT INTO group_members(group_id,slug,"rank") VALUES(?, ?, 0)', (gid, "x"))
    conn.execute("DELETE FROM groups WHERE id=?", (gid,))
    assert conn.execute("SELECT COUNT(*) FROM group_members").fetchone()[0] == 0
