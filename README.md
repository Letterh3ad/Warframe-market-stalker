# Warframe Market Stalker

A price-tracking and buy/sell timing tool for [warframe.market](https://warframe.market).
It stores order-book snapshots and daily/hourly statistics in a local SQLite database,
computes features (volatility, spread, mean-reversion, seasonality) over that history, and
runs a small set of pluggable analyzers that emit buy/sell signals with their evidence
attached. A background daemon adapts its own polling cadence per item, from a 30 minute
floor down to a 2 minute ceiling, based on how much is actually happening.

**This tool is read-only.** It never places, edits or cancels an order on
warframe.market, and it never logs in. It only reads public catalog, statistics and
order-book data and gives you information to act on yourself.

## Install

Requires Python 3.12+.

```bash
python -m venv .venv
.venv/Scripts/activate          # or source .venv/bin/activate on Linux/macOS
pip install -e .
```

This installs the `wfm` console script and its one runtime dependency, `httpx`. Without
`pip install -e .`, run everything as `python -m wfm <command>` instead.

## Setup

Copy the example config and adjust it:

```bash
cp wfm.toml.example wfm.toml
```

`wfm.toml` (gitignored) holds the daemon's pid file location, the adaptive polling
weights, and the tuned analyzer thresholds. See `wfm.toml.example` for every field and
its default; anything left out falls back to the built-in default in `wfm/config.py`.
Every field can also be set with an environment variable, `WFM_<FIELD_NAME>` (e.g.
`WFM_REQUESTS_PER_SECOND=2.5`), which takes priority over the file.

No API key or login is needed. The database (`wfm_market.db` by default) and item cache
are created on first run.

Rate safety, out of the box: the client sustains **2.8 requests/second** by default,
under the documented public ceiling of 3.0, at **concurrency 1** (never configurable
above either). A circuit breaker halts the daemon and emits an operational alert on
repeated 429 responses rather than retrying into a block. There is no code path that can
reach the network except through the single client instance holding this limiter; two
compliance tests enforce that directly.

## Command list

Run `wfm <command> --help` for a command's own flags. `--json` (before the command),
`--verbose` and `--config PATH` are global flags accepted by every command.

- **`wfm sync [--force] [--dry-run] [--status]`**: sync the catalog metadata for every
  item (version-gated, so an unchanged catalog costs one request). `--status` reports
  sweep state instead of running anything.
- **`wfm backfill (--all | --slug SLUG) [--limit N] [--dry-run]`**: backfill daily/hourly
  statistics from the v1 endpoint, for the whole catalog or one item.
- **`wfm search QUERY [--limit N]`**: fuzzy search the item catalog.
- **`wfm report [QUERY] [--group GROUP] [--rank RANK] [--refresh]`**: print a feature
  report (price, book, market, seasonality) for one item or a saved group. `--refresh`
  fetches the live order book first.
- **`wfm watch add QUERY [--rank RANK] [--pin WEIGHT] [--alert]`**: add an item to the
  watchlist the daemon polls.
- **`wfm watch rm QUERY [--rank RANK]`**: remove an item from the watchlist.
- **`wfm watch ls`**: list the watchlist.
- **`wfm watch suggest [--top N]`**: suggest watchlist candidates from the catalog
  without adding them.
- **`wfm group new NAME`** / **`wfm group rm NAME`** / **`wfm group ls`** /
  **`wfm group show NAME`**: manage saved item groups.
- **`wfm group add NAME QUERY [--rank RANK]`** / **`wfm group remove NAME QUERY [--rank RANK]`**:
  add or remove a member from a group.
- **`wfm validate --start YYYY-MM-DD --end YYYY-MM-DD [--analyzer NAME] [--horizon-days N] [--sweep KEY] [--values V1,V2,...]`**:
  replay an analyzer against stored history and report hit rate.
- **`wfm signals [--since WHEN] [--analyzer NAME] [--slug SLUG] [--limit N]`**: list
  stored signals. `--since` takes an ISO date or a duration like `7d`.
- **`wfm digest`**: run the daily digest send now (normally the daemon does this at
  09:00 local).
- **`wfm trade buy QUERY QUANTITY PLATINUM [--rank RANK] [--note NOTE]`** /
  **`wfm trade sell QUERY QUANTITY PLATINUM [--rank RANK] [--note NOTE]`**: record a
  trade in the ledger. This only records what you tell it; it never touches
  warframe.market.
- **`wfm holdings`**: current positions, marked to the last stored order book.
- **`wfm pnl [--realized] [--since WHEN]`**: realized/unrealized profit and loss (FIFO
  matched).
- **`wfm daemon start [--force]`** / **`wfm daemon stop`** / **`wfm daemon status`**: run,
  stop or check the background daemon (see below). `--force` clears an orphaned pid file
  first, for when a crashed daemon left one behind and the pid has since been reused by
  an unrelated process.
- **`wfm scan [--slug SLUG]`**: a one-shot manual poll of the watchlist (or one item),
  outside the daemon.

## The daemon

`wfm daemon start` runs one long-lived process that owns:

- the adaptive poll loop over your watchlist, scoring each item on volatility, volume,
  spread and a manual pin weight, and mapping that score to a polling interval between
  the 30 minute floor and the 2 minute ceiling;
- the daily catalog and statistics sweep, in a configured window;
- the 09:00 local digest send for signals that don't need live delivery.

`wfm daemon status` reports whether it's running, its pid, and how long since its last
heartbeat. `wfm daemon stop` requests a stop through a database flag rather than sending
a process signal (the only mechanism that works identically on Windows and POSIX); the
daemon exits cleanly after its current poll rather than being killed mid-request.

The daemon persists its schedule, so a restart resumes from where it left off instead of
starting cold: items overdue after downtime are caught up bounded and oldest-first rather
than all at once.

`wfm scan` is the stateless alternative for a manual, one-off poll; it never claims the
daemon's identity or its day, so it's safe to run alongside a live daemon.

## Development

```bash
pip install -e ".[dev]"
python -m pytest -q
```

Tests marked `live` hit the real warframe.market API and are deselected by default; run
them only when you mean to (`pytest -m live`).
