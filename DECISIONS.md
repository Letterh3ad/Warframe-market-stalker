# Decisions

Architecture and design decisions for Warframe Market Stalker.

## 2026-08-27 - Watchlist-driven tracking, not full-catalog polling

**Context:** Goal is "all items at all times", but live order books for 3,745 items every
30 min is ~250k requests/day and would get the client banned.

**Decision:** Catalog metadata syncs for all items daily. Live order-book polling only
covers a user-managed watchlist. `wfm watch add/rm/ls` manages it.

**Alternatives:** Full-catalog live polling (rate-limit suicide). Fixed liquidity tiers
(ignores what the user actually cares about).

## 2026-08-27 - Adaptive scheduler with a request budget

**Context:** User wants a 30 min baseline but to spend spare API capacity on items where
it matters.

**Decision:** 30 min is a per-item *floor*. A budget scheduler (~3 req/s) spends leftover
capacity on items scoring high on volatility, volume, spread, and user pin. Volatile items
can reach ~2 min cadence, stale ones stay at 30.

**Alternatives:** Flat 30 min for everything (wastes ~95% of available capacity).

## 2026-08-27 - Backfill history from the v1 statistics endpoint

**Context:** Snapshot polling alone means the first useful signal is weeks away. The repo
has 59 snapshots over 25 hours, effectively nothing.

**Decision:** Use v1 `/items/{slug}/statistics` for backfill. It returns 90 days of daily
candles (volume, OHLC, median, moving avg, Donchian) plus 48h hourly, from real closed
trades. Run once per item, then daily incrementals. v2 stays the source for live orders.

**Alternatives:** v2 only (no historical or volume data exists there). Waiting to
accumulate our own history (weeks of dead time, no volume data ever).

## 2026-08-27 - Full order book, not /top

**Context:** `/orders/item/{slug}/top` returns 5 orders per side. Many are stale listings
from years ago.

**Decision:** Use `/orders/item/{slug}` for the full book (700+ orders on popular items).
Compute depth curves and online-only best bid/ask, since only online sellers are tradeable.

**Alternatives:** `/top` (cheaper, but no depth and prices are not actionable).

## 2026-08-27 - SQLite, normalized, no raw JSON per row

**Context:** Current DB is 380 KB for 59 rows because every row stores `raw_top_json`.

**Decision:** Stay on SQLite with WAL mode. Split into `items`, `watchlist`,
`daily_stats`, `hourly_stats`, `order_snapshots`, `signals`. Drop per-row raw JSON, keep
raw only for a sampled subset.

**Alternatives:** Postgres or DuckDB (unnecessary complexity at this scale).

## 2026-08-27 - Rivens excluded

**Context:** Rivens price per roll and have no fungible price series.

**Decision:** Filter `tag=riven` out at catalog sync. No riven support.

## 2026-08-27 - CLI and alerts first, dashboard later

**Context:** Deciding surface area before building.

**Decision:** Ship a modular CLI (`sync`, `backfill`, `watch`, `scan`, `signals`, `report`)
plus an alerting path. Dashboard is a later phase.

## 2026-08-27 - Three analyzers behind a plugin registry

**Context:** "Buy/sell timing" means different math per trading horizon: minute-scale
flips, week-scale mean reversion, and day-scale sell timing on held stock.

**Decision:** All three, as pluggable analyzers behind one `Analyzer` protocol and a
registry. Adding a fourth is one file plus one registration line. Config enables or
disables each.

**Alternatives:** Picking one persona (leaves the other two unbuildable without a
rewrite). Three hardcoded paths (duplicated statistics, no extension point).

## 2026-08-27 - Trade ledger, holdings derived

**Context:** Sell-timing analysis needs to know what the user holds. Options were no
bookkeeping, a holdings table, or a full ledger.

**Decision:** A `trades` table records buys and sells. Holdings and realized P&L are views
over it, never stored. This also lets a validation harness replay real fills against
signal history to score whether each analyzer earns its keep.

**Alternatives:** Holdings-only table (same schema cost, no P&L history, no validation).
No inventory (sell timing becomes hypothetical).

## 2026-08-27 - Rank-aware storage, canonical rank as the CLI default

**Context:** A mod has no single price. Primed Continuity at rank 0 and rank 10 differ by
multiples, and v1 statistics already splits candles by `mod_rank`.

**Decision:** `(slug, rank)` is the key on every price-bearing table, `rank` defaulting to
0 for rankless items. CLI commands default to the item's canonical rank so ranks need not
be typed, with `--rank all` opening it up.

**Alternatives:** Canonical rank only (blind to the rank-0-buy, max-rank-sell flip).
Rank-aware with rank always explicit (correct but tedious).

## 2026-08-27 - Full-catalog daily backfill, watchlist-only order books

**Context:** Daily statistics cost one request per item, so a full sweep is ~21 minutes.
Order books are the expensive endpoint.

**Decision:** Backfill daily statistics for the entire catalog, then daily incrementals.
Order-book polling stays watchlist-only. This makes `watch suggest` useful on day one,
makes adding an item instant since its history already exists, and gives analyzers a
market baseline for cross-sectional comparison.

**Alternatives:** Watchlist-only backfill (circular: suggest has nothing to rank by, and
no analyzer can ask whether an item is cheap relative to the market).

## 2026-08-27 - Features computed in memory, not persisted

**Context:** Analyzers need shared derived statistics. A persisted `features` table was
proposed for auditability.

**Decision:** Features are pure functions computed per tick and handed to analyzers in
memory. The `features` table exists only for tests and debugging, written under
`--persist-features`, and nothing in the production path reads it. Consequence:
`signals.evidence_json` must be fully self-contained, since the "why" cannot be
reconstructed from a features row that may not exist.

**Alternatives:** Persisting features every tick (derived data written at high frequency
for no runtime reader). Analyzers querying SQLite directly (duplicates rolling-window math
three times, and every analyzer test needs a populated database).

## 2026-08-27 - Terminal is the base alert sink, Discord is secondary

**Context:** Alerts go to a terminal and optionally a Discord webhook. Which one is
load-bearing determines what happens when the other is absent or broken.

**Decision:** Terminal is always enabled and needs no configuration. Every signal lands
there, and `wfm signals` renders the same formatter over stored signals so live and
historical output share one code path. Discord is optional, mirrors a filtered subset, and
its delivery failures are logged but never propagate.

**Alternatives:** Discord-primary (the tool stops working without a webhook, and a dead
webhook silently loses signals).

## 2026-08-27 - Alert routing follows signal horizon

**Context:** A flip is worthless in an hour. A mean-reversion entry is just as good
tomorrow. One global alerting rule serves neither.

**Decision:** URGENT signals alert live above per-analyzer thresholds. DAILY signals batch
into one digest at 09:00 local. A per-item `alert_override` forces live delivery. Noise is
further controlled by per-item per-analyzer cooldowns, deduplication against still-open
signals, and a digest size cap.

**Alternatives:** One global threshold (either spams on slow signals or delays flips past
usefulness).

## 2026-08-27 - Daemon as the scheduler host

**Context:** Adaptive sub-30-minute polling needs in-memory budget and queue state.

**Decision:** A long-running single-threaded asyncio `wfm daemon` runs the sweep, the
watchlist poll loop and the digest timer. `wfm scan --once` remains as a stateless entry
point for manual runs.

**Alternatives:** Windows Task Scheduler every 30 min (hard-floors cadence, so adaptive
upscaling degrades to polling more items rather than polling hot items more often).

## 2026-08-27 - Conservative, centralized rate safety

**Context:** Explicit user requirement not to get rate-limited or blocked. Documented
limit is ~3 req/s.

**Decision:** One process-global token bucket at 2 req/s, concurrency 1, overridable
downward only. No code path issues an HTTP request except through the client holding it.
Retry-After honored, exponential backoff to a 5-minute cap, and a circuit breaker that
halts on three consecutive 429s rather than retrying into a block. Sweeps checkpoint per
item so a halt costs minutes. The scheduler cannot request directly, it asks a budget for a
grant, which is what bounds adaptive upscaling.

**Alternatives:** Per-component limiters (any new caller can bypass them). Parallel
requests (marginal speedup on a 21-minute sweep, real ban risk).

## 2026-08-27 - Prototype data discarded

**Context:** The old DB holds 59 snapshots over 25 hours across 6 slugs, `/top` only, no
volume, and is not comparable to the full-book schema.

**Decision:** Start with a fresh database. No migration. Backfill supplies 90 real days per
item within minutes.

**Alternatives:** Migrating (build time spent on data that can never influence a signal).

## 2026-08-27 - Rate ceiling set from published rules, not guesswork

**Context:** Requirement is to be as fast as the official documentation permits, with the
documentation as a hard limit. The rules at docs.warframe.market were read directly rather
than assumed.

**Decision:** Hard ceiling 3.0 req/s, the documented public limit, not configurable above
it. Default sustained 2.8, leaving headroom because the server's rate window and our clock
will not agree and sitting exactly on a documented limit still trips it. User-Agent takes
the documented form `WFMStalker/<version> (+<repo or contact>)`, asserted by a test, never
spoofing a browser. Both are covered by compliance tests. Full sweep is ~21 minutes.

**Alternatives:** 2.0 req/s (needlessly slow, my earlier guess). Exactly 3.0 (compliant on
paper, trips limiters in practice).

## 2026-08-27 - Read-only, permanently

**Context:** The published rules call automation and trade bots a grey area, expect
stricter limits, and reserve the right to restrict access without notice.

**Decision:** Automated trading is a permanent non-goal. The tool never places, edits or
cancels an order. It reads and advises only. Also documented: an explicit compliance
argument for why the daily full-catalog sweep satisfies the "do not repeatedly fetch large
collections" rule (once-ever history seed, incremental daily candles, `/versions`-gated
catalog, everything cached locally), plus a contained fallback to watchlist-only refresh
if that ever becomes unwelcome.

**Alternatives:** Leaving order placement open as a future option (invites building toward
the exact behavior the rules single out).

## 2026-08-27 - A services layer, enforced by a test

**Context:** The GUI will be a full click-through frontend with per-item and per-group
analysis. "The GUI is just a second frontend" was a promise with nothing enforcing it.

**Decision:** All use cases live in `services/`, returning plain data. `cli/` is a thin
shell over it and the GUI will be a second one. An import-linting test asserts no module
under `cli/` imports `store`, `api` or `analyzers`, so the boundary is enforced rather than
intended.

**Alternatives:** Logic in CLI modules (guarantees the GUI is a rewrite). A services layer
by convention only (erodes at the first deadline).

## 2026-08-27 - Scope-aware analyzer registry and persisted groups

**Context:** The GUI must analyze items individually and in user-defined groups. Every
analyzer designed so far is per-item.

**Decision:** `Analyzer` carries `scope: ITEM | GROUP`, with `GroupAnalyzer` taking a list
of FeatureSets. The registry and runner are scope-aware from the start though all three
shipped analyzers are ITEM-scoped. Groups persist in `groups` and `group_members`, so a
group built in the CLI is the group the GUI renders, and group membership is independent
of the watchlist.

**Alternatives:** Per-item only (retrofitting a second evaluation shape means refactoring
every call site). Groups as a GUI-only concept (the CLI could never analyze them).

## 2026-08-27 - Request priority classes over one shared bucket

**Context:** A GUI click on an uncached item needs rates immediately, but must not raise
the request rate.

**Decision:** Three grant classes, INTERACTIVE, BACKGROUND, BULK, against one shared
bucket, so priority changes ordering and never rate. Interactive grants carry a per-minute
cap so frontend interaction cannot starve the poll loop or push toward the limit.

**Alternatives:** One class (a GUI click queues behind a 21-minute sweep). A separate
bucket for interactive requests (two buckets means the real aggregate rate is their sum,
which is how limits get exceeded).

## 2026-08-27 - Designs tracked in `docs/design/`, tool scaffolding ignored

**Context:** The `superpowers` skill writes specs to `docs/superpowers/specs/` by default.
Gitignoring that directory to keep process scaffolding out of the repo also swept out the
design document itself, which was not the intent.

**Decision:** Separate the two. Design documents live in `docs/design/` and are tracked,
since the spec is the contract the implementation is reviewed against. `docs/superpowers/`
stays gitignored for skill output such as implementation plans. `DECISIONS.md` stays
tracked alongside.

**Alternatives:** Ignoring both (the repo then records what changed but never why an
alternative was rejected). Tracking the spec but ignoring `DECISIONS.md` (a spec goes
stale once built, while the rejected-alternatives record stays true, and git cannot
reconstruct it).

## 2026-08-27 - Migrations execute statements individually, not via executescript

**Context:** `executescript()` implicit-commits, which broke the transaction wrapper.

**Decision:** Execute statements individually inside the transaction so a migration is
atomic against its `PRAGMA user_version` bump, and keep the wrapper's COMMIT
unconditional so a future migration reaching for `executescript` fails loudly.

**Alternatives:** Relaxing the wrapper to tolerate an absent transaction, which fixes the
symptom and silently abandons atomicity, leaving a crash mid-migration with tables
created and the version un-bumped.

## 2026-08-28 - transaction() is reentrant via savepoints, and IMMEDIATE at the top level

**Context:** Every repository write method opens its own transaction, so no operation
spanning two repositories (create a group and add its members) could be made atomic.
Separately, `BEGIN` is DEFERRED, and the daemon and CLI share one WAL database.

**Decision:** A nested `transaction()` becomes a `SAVEPOINT`; the outermost one issues
`BEGIN IMMEDIATE`. The unconditional COMMIT stays, so an `executescript()` that
implicit-commits still fails loudly in both branches.

**Alternatives:** Repository methods taking an optional connection-or-transaction
argument, which pushes atomicity bookkeeping onto every call site; leaving DEFERRED and
relying on the busy timeout, which cannot resolve `SQLITE_BUSY_SNAPSHOT` on a write
upgrade.

## 2026-08-28 - Raw payload sampling is hashed, not counted

**Context:** `RawSnapshotsRepo` sampled off an instance counter, but every other
repository here is a stateless wrapper over a connection, so one instance per call is
the natural pattern and stored 100% of payloads: exactly the raw-JSON bloat the storage
design exists to avoid.

**Decision:** Sample off `crc32("slug|rank|utc_iso") % sample_rate`, so the decision is
stateless and reproducible. Measured 2.1% at a configured 2% and 10.3% at 10% over
realistic key streams.

**Alternatives:** Persisting the counter (a write per skipped snapshot), or making the
repository a process-lifetime singleton (a lifecycle rule the rest of the package does
not have and nothing would enforce).

## 2026-08-28 - Missing-row writes raise, except sweep halt which upserts

**Context:** `SweepStateRepo.checkpoint`/`finish`/`halt` were bare `UPDATE`s that
silently no-op on an unknown sweep name.

**Decision:** `checkpoint` and `finish` raise `KeyError`. `halt` upserts, because it is
the circuit breaker's record of why the next run must not charge back in, and it has to
land even when `start()` is what failed.

**Alternatives:** Raising uniformly, which loses the halt record in the one case that
matters most; upserting uniformly, which turns a typo into a sweep that silently never
advances.

## 2026-08-28 - FakeClock.sleep yields to the event loop

**Context:** The phase 2 plan's fake clock advanced time without awaiting anything, so
`await clock.sleep(x)` was not a suspension point. Concurrent tasks then ran to
completion one at a time in creation order, and no test of contention could observe a
queue forming.

**Decision:** `FakeClock.sleep` advances time and then `await asyncio.sleep(0)`, so it
suspends like the real thing while still taking no wall clock time.

**Alternatives:** Driving the tests with real `asyncio.sleep`, which makes the phase 7
scheduling tests take a simulated day; asserting on internal state instead of observed
ordering, which tests the implementation rather than the behaviour.

## 2026-08-28 - Budget waiters are a priority heap, and a cancelled waiter is dropped

**Context:** The budget hands the single bucket slot to the highest priority waiter. A
waiter cancelled while queued (Ctrl+C, a cancelled daemon task) stayed in the heap.

**Decision:** `_enter` removes its own entry on cancellation and `_leave` skips
cancelled futures. Without this, handing the slot to a cancelled future raises
`InvalidStateError` and strands every waiter behind it, which stops all pacing.

**Alternatives:** `asyncio.PriorityQueue` of tickets, which has the same cancellation
hole one layer down; a plain lock, which loses priority ordering entirely.
