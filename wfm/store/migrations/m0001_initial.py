from __future__ import annotations

import sqlite3

DDL = """
CREATE TABLE items (
    slug              TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    url_name          TEXT NOT NULL,
    tags              TEXT NOT NULL DEFAULT '[]',
    max_rank          INTEGER NOT NULL DEFAULT 0,
    canonical_rank    INTEGER NOT NULL DEFAULT 0,
    ducats            INTEGER,
    is_set            INTEGER NOT NULL DEFAULT 0,
    last_seen_version TEXT
);
CREATE INDEX idx_items_name ON items(name);

CREATE TABLE daily_stats (
    slug       TEXT    NOT NULL,
    "rank"     INTEGER NOT NULL DEFAULT 0,
    date       TEXT    NOT NULL,
    volume     INTEGER,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    median     REAL,
    avg_price  REAL,
    wa_price   REAL,
    moving_avg REAL,
    donch_top  REAL,
    donch_bot  REAL,
    PRIMARY KEY (slug, "rank", date)
) WITHOUT ROWID;

CREATE TABLE hourly_stats (
    slug      TEXT    NOT NULL,
    "rank"    INTEGER NOT NULL DEFAULT 0,
    ts        TEXT    NOT NULL,
    volume    INTEGER,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    median    REAL,
    avg_price REAL,
    wa_price  REAL,
    PRIMARY KEY (slug, "rank", ts)
) WITHOUT ROWID;

CREATE TABLE order_snapshots (
    slug             TEXT    NOT NULL,
    "rank"           INTEGER NOT NULL DEFAULT 0,
    ts               TEXT    NOT NULL,
    best_bid         INTEGER,
    best_ask         INTEGER,
    online_best_bid  INTEGER,
    online_best_ask  INTEGER,
    bid_depth_1      INTEGER,
    bid_depth_2      INTEGER,
    bid_depth_3      INTEGER,
    bid_depth_4      INTEGER,
    bid_depth_5      INTEGER,
    ask_depth_1      INTEGER,
    ask_depth_2      INTEGER,
    ask_depth_3      INTEGER,
    ask_depth_4      INTEGER,
    ask_depth_5      INTEGER,
    bid_count        INTEGER NOT NULL DEFAULT 0,
    ask_count        INTEGER NOT NULL DEFAULT 0,
    online_bid_count INTEGER NOT NULL DEFAULT 0,
    online_ask_count INTEGER NOT NULL DEFAULT 0,
    stale_share      REAL,
    PRIMARY KEY (slug, "rank", ts)
) WITHOUT ROWID;

CREATE TABLE order_snapshots_raw (
    slug    TEXT    NOT NULL,
    "rank"  INTEGER NOT NULL DEFAULT 0,
    ts      TEXT    NOT NULL,
    payload TEXT    NOT NULL,
    PRIMARY KEY (slug, "rank", ts)
) WITHOUT ROWID;

CREATE TABLE signals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    slug          TEXT    NOT NULL,
    "rank"        INTEGER NOT NULL DEFAULT 0,
    analyzer      TEXT    NOT NULL,
    ts            TEXT    NOT NULL,
    horizon       TEXT    NOT NULL,
    direction     TEXT    NOT NULL CHECK (direction IN ('buy','sell','hold')),
    magnitude     REAL    NOT NULL,
    confidence    REAL    NOT NULL,
    evidence_json TEXT    NOT NULL,
    expires_at    TEXT,
    alerted_at    TEXT
);
CREATE INDEX idx_signals_item ON signals(slug, "rank", analyzer, ts DESC);
CREATE INDEX idx_signals_undelivered ON signals(horizon, ts) WHERE alerted_at IS NULL;

CREATE TABLE trades (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    slug     TEXT    NOT NULL,
    "rank"   INTEGER NOT NULL DEFAULT 0,
    ts       TEXT    NOT NULL,
    side     TEXT    NOT NULL CHECK (side IN ('buy','sell')),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    platinum INTEGER NOT NULL CHECK (platinum >= 0),
    note     TEXT
);
CREATE INDEX idx_trades_item ON trades(slug, "rank", ts);

CREATE TABLE watchlist (
    slug           TEXT    NOT NULL,
    "rank"         INTEGER NOT NULL DEFAULT 0,
    added_at       TEXT    NOT NULL,
    pin_weight     REAL    NOT NULL DEFAULT 0,
    alert_override INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (slug, "rank")
) WITHOUT ROWID;

CREATE TABLE groups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE group_members (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    slug     TEXT    NOT NULL,
    "rank"   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (group_id, slug, "rank")
) WITHOUT ROWID;

CREATE TABLE sweep_state (
    sweep      TEXT PRIMARY KEY,
    cursor     TEXT,
    started_at TEXT,
    updated_at TEXT,
    status     TEXT NOT NULL,
    reason     TEXT,
    done_count INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;

CREATE TABLE features (
    slug         TEXT    NOT NULL,
    "rank"       INTEGER NOT NULL DEFAULT 0,
    ts           TEXT    NOT NULL,
    payload_json TEXT    NOT NULL,
    PRIMARY KEY (slug, "rank", ts)
) WITHOUT ROWID;

CREATE TABLE http_cache (
    url           TEXT PRIMARY KEY,
    etag          TEXT,
    last_modified TEXT,
    fetched_at    TEXT NOT NULL,
    body          TEXT NOT NULL
) WITHOUT ROWID;

CREATE VIEW holdings AS
SELECT
    slug,
    "rank",
    SUM(CASE side WHEN 'buy' THEN quantity ELSE -quantity END) AS quantity,
    CASE
        WHEN SUM(CASE side WHEN 'buy' THEN quantity ELSE 0 END) > 0
        THEN CAST(SUM(CASE side WHEN 'buy' THEN quantity * platinum ELSE 0 END) AS REAL)
             / SUM(CASE side WHEN 'buy' THEN quantity ELSE 0 END)
    END AS avg_cost
FROM trades
GROUP BY slug, "rank"
HAVING quantity > 0;
"""


def up(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
