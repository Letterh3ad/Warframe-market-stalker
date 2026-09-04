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

## 2026-08-28 - httpx with async throughout, and one client owns the only transport

**Context:** The design named an asyncio daemon but no HTTP library, and the read-only
guarantee needs to be structural rather than a convention.

**Decision:** `httpx.AsyncClient`, constructed only inside `WFMClient`, which exposes
`get_json` and no write verb. Two compliance tests enforce it: one fails if any module
under `wfm/` outside `client.py` constructs a transport, another if a write verb ever
reaches a transport anywhere in `wfm/`.

**Alternatives:** `requests` with threads, which makes the phase 7 scheduler a thread
pool and the timing tests wall clock bound; aiohttp, whose test story has no equivalent
of `httpx.MockTransport`.

## 2026-08-28 - Backoff is held on the client, not on the request

**Context:** The phase 2 constraints require a global backoff, but the planned client
slept inside the retry loop of whichever call got the 429, so a concurrent caller could
issue a request into the same block.

**Decision:** `WFMClient` holds a `_hold_until` instant that every caller waits out
before acquiring the budget. A 429 is a statement about the client's IP, not about one
request.

**Alternatives:** Relying on concurrency 1, which is a config value rather than a
guarantee, and would silently stop protecting anything the day concurrency changes.

## 2026-08-28 - Exceeding the interactive per-minute cap demotes rather than blocks

**Context:** INTERACTIVE requests need a cap so a frontend cannot starve the poll loop.

**Decision:** Over the cap, the request is served at BACKGROUND priority. The cap orders
work; it is not a second rate limit, and rejecting would make a GUI feel broken.

**Alternatives:** Rejecting with an error, or blocking until the sliding window frees a
slot, which turns a busy minute into an unexplained freeze.

## 2026-08-28 - Parsers read fields tolerantly

**Context:** v2 has been observed using camelCase where v1 uses snake_case, and the
project depends on payload shapes it does not control.

**Decision:** Every field read goes through `_pick`, which takes several candidate names
and a default, so an upstream rename degrades to a missing field rather than a crash.
The live contract test is what proves the real shapes still parse.

**Alternatives:** Pydantic models with strict validation, which turns any upstream
rename into a total outage of the sweep.

## 2026-08-28 - Catalog version token lives in sweep_state.cursor

**Context:** The v2 `/versions` token gates the catalog sync so an unchanged catalog
costs one request instead of ~3745. It needs somewhere to persist between runs.

**Decision:** Store it in `sweep_state.cursor` for the `catalog` sweep. It is exactly a
sweep cursor: the marker for what the last completed pass covered. No new table.

**Alternatives:** A dedicated `catalog_version` table or a row in a generic kv table,
both of which add a migration for a single string.

## 2026-08-28 - Sweep skips a per-item ApiError, halts on CircuitOpen

**Context:** During a full catalog sweep an individual item can 404 (delisted slug),
which is unrelated to the client's standing. A run of 429s is not.

**Decision:** `run_sweep` logs and skips a per-item `ApiError` (one bad slug must not
cost a 21-minute sweep) and halts immediately on `CircuitOpen`, recording the reason in
`sweep_state`. Continuing past a tripped breaker is what turns a rate limit into a block.

**Alternatives:** Halting on any error (a single delisting stops the sweep) or retrying
per item (hammers the API the breaker just protected).

## 2026-08-28 - A halted sweep resumes from its cursor, only a finished one restarts

**Context:** The task 5 plan resumed only when `sweep_state.status == "running"`, so a
breaker trip at item 3700/3745 meant the next run re-fetched all 3745.

**Decision:** `run_sweep` resumes when status is `running` or `halted` (the cursor is
preserved in both). Only `done` starts over, which is the intended daily behaviour.

**Alternatives:** Requiring a manual reset after a halt, which the phase 7 daemon would
have to special-case anyway.

## 2026-08-28 - CLI entrypoint via `python -m wfm` and a console script

**Context:** The phase 3 plan wrote `wfm ...` invocations but produced no runnable
entrypoint (no `__main__.py`, no `[project.scripts]`).

**Decision:** Added `wfm/__main__.py` and a `wfm = "wfm.cli.main:main"` console script.
`python -m wfm` works immediately; the `wfm` script needs `pip install -e .` to appear.

**Alternatives:** Only the console script (needs a reinstall to exist) or only
`__main__.py` (leaves `wfm` unbound).

## 2026-09-01 - A window statistic needs 90% coverage, not a perfectly full window

**Context:** Phase 4 planned that a window statistic is `None` unless its window is fully
populated, so a "30 day median" can never come from four points. On real data this nulled
every long window in the catalog: warframe.market publishes closed days only and never
returns more than 89 daily candles, so a 90-of-90 rule left `median_90d`, `mad_90d`,
`robust_z` and `percentile_90d` permanently unavailable for all 3839 items. Those are
exactly the inputs phase 5's mean-reversion analyzer needs.

**Decision:** `wfm/features/price.py` gates each window on `MIN_COVERAGE = 0.9`
(`ceil(days * 0.9)` samples: 81 of 90, 27 of 30, 7 of 7). The guard still refuses thin
history, which is its purpose, but tolerates the API's shape and genuine gaps.

**Alternatives:** Requiring >=85 samples for the 90 day window only (arbitrary, patches
one window). Redefining the long window as 89 days (exact today, breaks if the API ever
returns 88 or 90). Leaving it strict and having phase 5 use the 30 day window instead.

## 2026-09-01 - Feature windows anchor on the newest complete day, not on today

**Context:** Windows ended at `now.date()`, but the newest candle is always yesterday
because the API publishes complete days only. Every window was therefore a day short. For
the 7 day market return that was fatal: it needs 8 closes to span 7 intervals, got 7, and
returned `None` for every item, leaving the entire market block silently empty.

**Decision:** `feature_service._anchor_date` ends windows at the newest date in
`daily_stats`, capped at the injected `now` so a clock set before the data cannot read
candles that had not happened yet. `market_context` takes `now` explicitly rather than
reading wall-clock time, which also makes the block deterministic under `FakeClock`.

**Alternatives:** Asking for one extra day at each call site (a magic +1 encoding the
API's lag everywhere). Anchoring per item (an item with stale data would look current).

## 2026-09-01 - The market sample strides across the catalog

**Context:** `market_context` sampled `all_slugs()[:500]` to avoid a full catalog pass per
tick. `all_slugs()` is alphabetical, so the "sample" was just the "a" items
(`abating_link` to `ayatan_hemakara_sculpture`). Its tag mix inverted the real catalog
(relic 155 > mod 68, against mod 1045 > relic 575), so the market median misreported the
market and every tag sorting late got `cohort_size: 0` and lost its cohort comparison.

**Decision:** Sample with a stride across the ordered slug list. Same cost, still
deterministic, and it tracks the true tag distribution.

**Alternatives:** A random sample (not reproducible run to run). A full catalog pass
(dominates the tick cost for a figure that moves slowly).

## 2026-09-01 - Order sides compare against `Side`, not `Direction`

**Context:** The phase 4 plan wrote `wfm/features/book.py` comparing `order.side` against
`Direction.SELL`/`Direction.BUY`. `Order.side` is typed `Side` and `parse_orders` produces
`Side`. The two are distinct enum classes, so the identity check is `False` for every real
order and every live book would have aggregated to empty. The plan's own tests built
orders with `Direction` too, so they passed and hid it.

**Decision:** `book.py` compares against `Side` throughout, with a regression test that
feeds `summarize` the output of `parse_orders` rather than hand-built orders, so the
enum the API actually produces is the one under test.

**Alternatives:** Collapsing `Side` into `Direction` (loses the "HOLD is not a trade"
distinction that `Side` exists to enforce).

## 2026-09-01 - Window coverage is measured in calendar days, not data points

**Context:** The 90% coverage gate counted entries in a pre-filtered close list, so an
illiquid item with 27 closes spread over three months cleared the "30 day" gate and got a
median labelled 30d that was computed over 86 days. warframe.market omits untraded days
entirely, so that is the normal shape for an illiquid item, and it is exactly the
plausible-looking wrong number the guard exists to prevent.

**Decision:** `price.window()` selects candles by date range from the newest candle back
`days` calendar days, then requires `MIN_COVERAGE` of those days to carry a close.
`market.returns_over` anchors the same way. `atr` and `donchian_position`, which had no
guard at all, now take a covered window.

**Alternatives:** Requiring strictly consecutive days (rejects any real series, since
untraded days are normal).

## 2026-09-01 - An item is excluded from its own tag cohort

**Context:** `build_context` included every sampled item in its own tag's median, so an
item that was the only sampled member of its tag was benchmarked against itself and
reported `excess_return_7d` of exactly 0.0 with `cohort_size: 1`: a confident-looking
reading that is pure self-comparison. Separately, `tag_median_return_7d` fell back to the
market-wide median whenever the tag had no cohort, so a market number travelled under a
tag-specific name.

**Decision:** `market.build` takes the item's `slug` and drops it from its cohort before
taking the median. `cohort_size` counts peers, so a solo member reports 0.
`tag_median_return_7d` is `None` when there is no cohort; `excess_return_7d` then falls
back to the item-exclusive market median, which is the number reported as
`market_median_return_7d` (see the 2026-09-02 entry: the reported field is the benchmark
actually used, not the item-inclusive median).

**Alternatives:** Requiring `cohort_size >= 2` while leaving the item in (the item still
skews a small cohort's median).

## 2026-09-01 - Hourly retention raised to 42 days for seasonality

**Context:** The hour-of-week profile has 168 buckets, so a bucket recurs once a week and
needs at least 4 weeks to reach `min_samples = 4`. Phase 3 pruned hourly rows at 14 days,
which capped every bucket at 2 samples, held `confidence` at 0.5 and left
`best_bucket_next_48h` permanently `None`, disabling any analyzer that gates on it.

**Decision:** `HOURLY_RETENTION_DAYS` 14 -> 42, and the feature window follows. Note the
API returns only 48 hours of hourly statistics per fetch, so this history accrues by
repeated polling rather than arriving in a backfill: seasonality stays unavailable until
the phase 7 daemon has been running for several weeks. That is reported honestly through
`confidence` and the provenance sample counts.

**Alternatives:** Lowering `min_samples` to 2 (a weekly rhythm inferred from two
observations is what the confidence gate exists to distrust). Deferring seasonality to
phase 7 entirely.

## 2026-09-02 - Delta review of the windowing rewrite: four fixes before merge

**Context:** A `/code-review high` pass over the two post-review commits on
`phase-4-features` (`852d0c0..b79b7b0`) returned ten findings. Four were real and cheap;
the rest were latent paths unreachable on warframe.market's real payload shape
(`donch_top`/`donch_bot` and `closed_price` are always present) or an efficiency
regression better handled in the phase 7 hot-path pass.

**Decision:** Applied four fixes with regression tests.

1. **Provenance completeness.** `price.build`'s `samples` gained `range_14d` and
   `volume_7d`. `atr_14d` gates on high/low coverage and `volume_trend` on the 7 day
   volume window, but neither had a counter, so a null could not explain itself, which
   the block's stated contract forbids.
2. **`MarketContext` carries its anchor.** `report_group` builds the context once and
   reuses it per member; re-deriving the anchor inside each `build_for` let a mid-run
   sync (or a midnight-UTC roll) measure the item's own 7 day return over a different
   window than its peers. `market_context` now records `anchor` on the context and
   `build_for` measures the item against that same date.
3. **`market_median_return_7d` is the benchmark actually used.** It now reports the
   item-exclusive market median, which is what `excess_return_7d` falls back to when the
   item has no cohort. Previously it reported the item-inclusive median, so a consumer
   recomputing `own - market_median_return_7d` got a different number than the reported
   excess. Every other field in the block is already item-relative.
4. **Seasonality staleness is visible, not erased.** `observed_age_hours` and
   `observed_bucket` now carry the newest observation even when it is too old to describe
   the present, so a dead feed with history is distinguishable from an item with none.
   The newest observation is also excluded from its own bucket's expectation profile
   whether or not it is fresh, so a feed that died a multiple of 168h ago cannot pad the
   `min_samples` gate.

**Alternatives:** Fixing the `market_median_return_7d` inconsistency by correcting the
DECISIONS.md wording instead of the code (leaves the reported field and the used
benchmark different). Threading the anchor through `report_group`'s call chain by hand
rather than on the context object (the context is the thing reused, so it is the thing
that should pin the window). Deferred, with a HANDOFF note: `market.build`'s `slug`
default of `None` (latent, `feature_service` always passes it), the `donchian_position`
fallback coverage gate and `in_window(end=None)` close anchoring (both unreachable on
real payloads), and `in_window` re-sorting/re-parsing dates per call (efficiency, phase 7).

## 2026-09-02 - Phase 5 analyzers: tuned thresholds and the shape of the analyzer layer

**Context:** Phase 5 added three analyzers (`flip`, `revert`, `selltime`) behind the
`Analyzer` protocol and a registry, a runner in `analysis_service`, a replay harness, and
`wfm validate`. Task 9 tunes the thresholds against `wfm_market.db` (3839 items, daily
candles 2026-06-04..2026-08-31) before the numbers go live.

**Decision:**

1. **`revert.z_threshold = 2.0`; `flip.min_margin_pct = 0.15`, `flip.min_margin_plat = 12`.**
   Written to `wfm.toml.example`. The stored history is too short to validate `revert` at
   its design horizon: `robust_z` needs 81 closes in a 90 day window, the DB holds ~89
   days total, so `robust_z` is null for every replay date through 2026-08-22 and only
   ~30% of items acquire one by 2026-08-31. Combined with the harness end boundary (point
   6), the only usable replay is `--start 2026-08-23 --end 2026-08-28 --horizon-days 3`,
   three scored days. Over it the `z_threshold` sweep 1.0/1.5/2.0/2.5/3.0 gave hit rates
   0.73/0.73/0.73/0.71/0.72 on 798/390/337/206/119 signals, median forward return 0.0
   except -0.07 at z=3.0. Hit rate is flat, so 2.0 is chosen as the joint peak and a
   standard 2 sigma cut, not because it is clearly best: 1.0-1.5 fire on nearly every
   eligible item, 2.5-3.0 thin out without buying accuracy. The confirmation rerun with
   the written `wfm.toml` reproduced the sweep's z=2.0 row exactly (337 signals, 247
   hits, hit rate 0.733). `flip` could not be tuned at all: the harness has no order-book
   history, so every `min_margin_pct` in 0.10/0.15/0.20/0.30 produced zero signals. Its
   values are design defaults; real validation is a phase 7 live watch.

2. **Dedup and cooldown live in `analysis_service`, not the analyzers.** An analyzer is a
   pure `FeatureSet -> list[Signal]` function with no memory. Suppression against open
   signals and the per-item per-analyzer cooldown are a persistence concern and sit with
   the code that already holds `SignalsRepo`.

3. **`analyze_item` / `analyze_group` are synchronous.** Feature assembly is in memory and
   the analyzers do no IO, so async bought nothing. The runner is a plain function.

4. **Online-only depth-curve primitive.** `BookFeatures.online_bid_depth` /
   `online_ask_depth` are tuples of cumulative quantity at the best N online prices, added
   to the feature layer and persisted by migration `m0002` (five `online_{bid,ask}_depth_N`
   columns on `order_snapshots`, executed statement by statement like `m0001`). Offline
   orders are excluded: an offline wall does not stop an online fill and must not read as
   proof a price is real. `flip` reads `[0]`, `selltime` reads `[-1]`.

5. **`selltime` replay uses a synthetic one-unit holding, and the harness replays the
   rank-0 daily series only.** The harness has no ledger, so it injects
   `Holding(quantity=1, avg_cost=last_close)` for every item to exercise the ledger-gated
   path. Its unrealized-P&L and sizing outputs under replay are therefore not meaningful;
   only the list-now / hold / wait decision and its forward return are. Rank is hardcoded
   to 0 for the target series, the synthetic holding and the `FeatureSet`, whereas
   production `feature_service.market_context` keys on `item.canonical_rank`, so for a
   ranked mod whose canonical rank is non-zero production evaluates a different price
   series and a replayed hit rate for that item does not transfer directly.
   `canonical_rank` is not threaded through the harness; phases 6-7 re-tune against
   accrued live data.

6. **Harness `_load` end-boundary limitation.** Candles load only up to the replay
   `--end`, so a signal emitted within `--horizon-days` of `--end` has no forward candle
   and is silently dropped from the scored count. Mitigation: choose `--end` at least
   `horizon-days` before the newest candle. This DB leaves no room at the 7 day horizon,
   which is why Task 9 tuned at a 3 day horizon and documented the ambiguity rather than
   reporting a clean number.

**Alternatives:** Picking the highest `z_threshold` that still fired (3.0) for the
tightest filter (rejected: no better hit rate, 119 signals over three days is not an
interpretable sample). Tuning `revert` over the brief's `--start 2026-06-01 --end
2026-08-20` window (rejected: `robust_z` is null across all of it, every value scores
zero). Deriving flip numbers from the price side (rejected: the analyzer is defined on
the order book; a price-only proxy validates a different thing). Loading forward candles
past `--end` to fix the end boundary (deferred: it widens the lookahead surface the
harness exists to control and needs its own review).

## 2026-09-03 - Phase 6: alert delivery, the ledger, and analyzer discovery

**Context:** Phase 6 delivers persisted signals to a terminal that always works and an
optional Discord webhook that may fail without losing anything, and records trades so
holdings and P&L derive from one source. The phase-6 plan predates phases 1-5.

**Decision:**

1. **One formatter, two readers.** `wfm/alerts/format.py::render_signal` /
   `render_digest` render both the live terminal sink and `wfm signals`, so a stored
   signal reads weeks later exactly as it did when it fired. `EVIDENCE_ORDER` names the
   leading evidence keys per analyzer; unknown keys still render.

2. **`signals.alerted_at` is the idempotency key.** The terminal sink is the sink of
   record: a signal is marked delivered once it has printed there, whether or not the
   optional Discord mirror also succeeded. `alert_service.deliver` does this per signal;
   `run_digest` marks the whole DAILY batch once the terminal sink returns without
   error, and surfaces any Discord failure in the result rather than withholding the
   mark (an earlier draft withheld it, which let a permanently broken webhook reprint a
   growing digest forever). The digest is rendered with no cap so every pending signal
   is in the text before it is marked; nothing is collapsed away and then lost.

3. **Routing is a pure function.** `alerts/routing.py::route` maps a signal to sink
   names with no transport: terminal always; Discord only for URGENT signals past both
   `discord_min_confidence` and `discord_min_magnitude`, or anything with a per-item
   `alert_override`. `deliver` only acts on URGENT signals and DAILY signals whose item
   carries an `alert_override`; a plain DAILY signal is left untouched for `run_digest`,
   the sole path that batches DAILY signals to Discord. DAILY signals never go live
   otherwise.

4. **Discord sink is the one module allowed a write.** `alerts/discord.py` constructs
   its own `httpx.AsyncClient` and issues exactly one `.post`, to its configured
   webhook. The two read-only compliance tests were narrowed to exempt that file
   (renamed `test_only_the_client_and_discord_sink_construct_an_http_transport` and
   `test_no_write_verb_reaches_the_transport_except_in_the_discord_sink`) and a new
   `test_the_discord_sink_posts_only_to_its_configured_webhook` pins the exemption down.
   The Discord mirror is best-effort and lossy by design: a multi-chunk message that
   fails partway is not retried, and a transient 5xx/429 is not distinguished from a
   permanent failure. The terminal sink holds the full content; Discord retry/backoff
   is a phase 7 daemon concern, not built here.

5. **FIFO realized P&L, not average cost.** `ledger/pnl.py::realized` matches sells to
   buys first-in-first-out per `(slug, rank)`, so a lot report shows which specific buys
   a sale closed out. `Trade.side` is `Side` (the 2026-09-01 decision), used throughout
   `pnl.py` and `ledger_service.record` where the phase's draft said `Direction`.

6. **Holdings stay a view read.** `ledger_service.holdings` takes `quantity`/`avg_cost`
   from the `holdings` SQL view and marks each position to the last stored book
   (`online_best_bid`, else `best_bid`). The view's `avg_cost` still blends closed-out
   lots (open since phase 1); that is a display number, and realized P&L already uses the
   FIFO matcher. Fix when the phase 7 P&L polish lands.

7. **Analyzer discovery.** `registry.discover()` imports every `wfm/analyzers/*.py` that
   exposes `ANALYZER`, in sorted order, so a new analyzer file needs no edit to the
   registry. A module that fails to import is logged and skipped, not fatal, so a broken
   scratch file does not take down every command that pulls in the registry. `python -m
   wfm` already worked (`3df0a7e`).

8. **`_forward_return` lookahead cap.** The replay harness credited a signal with the
   first candle on or after the horizon date with no upper bound, so a sparse series
   turned an N-day forward return into a much longer swing. Beyond `_FORWARD_SLACK_DAYS`
   (3, capped at `horizon_days`) past the target the signal is left unscored; the target
   series is loaded that far past `--end` so signals near the boundary can still resolve.
   Carried from the phase 5 re-review.

9. **`wfm pnl --since` filters lots, not the FIFO input.** Realized P&L runs FIFO
   matching over every trade, then keeps the lots sold on or after `--since`. Filtering
   the trade list first would strand a windowed sale against an empty buy queue and drop
   its realized profit.

10. **Trades resolve strictly.** `ledger_service.record` refuses a name that matches
    more than one catalog item unless it is an exact name or slug, rather than recording
    money against `matches[0]`. The read-only `catalog_service.resolve` keeps its lenient
    first-match behaviour (phase 3 deferred item 12).

**Alternatives:** Per-message Discord POSTs rather than one batched post (rejected:
rate-limit exposure, no benefit). Withholding the digest mark until every sink succeeds
(rejected: a broken webhook then reprints a forever-growing digest and the overflow past
the render cap is lost; the terminal is a sufficient sink of record). Deriving holdings
`avg_cost` from the FIFO remainder now (deferred: no behavioural difference for
single-lot positions, and it belongs with the phase 7 P&L work). An entry-point plugin
system for analyzers (rejected: package-dir scan is enough for a single-repo tool).

## 2026-09-03 - Validation harness: per-item isolation and canonical_rank threading

**Context:** Two phase 5 deferrals tagged for phase 7 (2026-09-02 entry, point 5, and the
harness's own module docstring). `replay` called `analyzer.evaluate` directly, so one
item's exception aborted a multi-thousand-item replay with nothing salvaged. It also
hardcoded rank 0 for every item, while production `feature_service.market_context` reads
`item.canonical_rank`; about 38% of a 500-item market sample carries a non-zero rank, so
the harness was tuning a different series than the one that ships for those items.

**Decision:** Route per-item evaluation through `wfm/analyzers/runner.py::run_item`,
which already isolates analyzer exceptions. `run_item`'s `skipped` return also fires when
a feature set lacks required features (e.g. `flip` needs book data the harness never
builds); that is expected, not a failure, so the harness only counts a skip as a failure
when the feature set was covered and the analyzer still didn't run. `ReplayResult` gained
`failures` and `failed_slugs` (additive, existing keys unchanged). Rank is now read from
`Item.canonical_rank` per slug, matching `market_context`, and threaded through the
target/sample series load, the `FeatureSet`, and the synthetic `Holding`. No analyzer
threshold was retuned; a changed replay number for a ranked item is expected and belongs
to the phase 7 live watch, not this task.

**Alternatives:** Reporting failures as a plain exception count with no slugs (rejected:
the brief requires seeing which items failed, not just how many). Adding a `rank`
override parameter to `replay` (rejected: no existing signature slot for it and no
caller needs to override per-item rank; `canonical_rank` is the single source of truth
production also uses).

## 2026-09-03 - watch_service anchors on ctx.clock; holdings avg_cost is the FIFO remainder

**Context:** Two deferred defects flagged as phase 7 opening cleanup (2026-09-02 entry,
point 6; task-0b brief).

**Decision:** `watch_service.suggest` called `DailyStatsRepo.window` without `end=`, so it
anchored on real wall-clock time rather than `ctx.clock`, the same defect phase 4 fixed in
`feature_service`. `feature_service._anchor_date` is promoted to `anchor_date` (module-
level, no longer private) and `watch_service.suggest` now anchors its window on it.

The `holdings` SQL view's `avg_cost` blends every buy including lots a sale has already
closed out. `wfm/ledger/pnl.py::_match` (the existing FIFO matcher, refactored to also
return the unmatched buy queues) backs a new `cost_basis(trades)` that averages only the
FIFO remainder. `ledger_service.cost_basis` wraps it; both `ledger_service.holdings` and
`analysis_service.build_context` now override the view's `avg_cost` with it, keeping
`quantity` from the view (already correct) unchanged. `build_context` needed the same
fix, not just `ledger_service.holdings`, because it queries `ctx.trades.holdings()`
directly rather than through the ledger service; without it `Holding.avg_cost` reaching
`selltime` through `build_context` would have stayed on the blended figure. This does
move `selltime`'s inputs for any item with a closed-out lot; no analyzer threshold was
retuned to compensate, per the brief.

**Alternatives:** Computing the corrected `avg_cost` inside `TradesRepo.holdings()`
itself (rejected: pulls FIFO matching, a business rule, into the store layer, which
elsewhere only depends on `wfm.store` and `wfm.models`). Leaving `analysis_service`
unfixed since the brief's file list didn't name it (rejected: the brief's own note says
the fix must reach `selltime` through `build_context`, and `build_context` bypasses
`ledger_service.holdings` entirely, so the fix has to be duplicated at its call site or
shared via a service-level helper; chose the shared helper).

## 2026-09-03 - Daemon runner: max_iterations exhaustion is not a stop, and a pending stop is honoured before mark_started

**Context:** Task 4 (phase 7), the single-loop daemon (`wfm/daemon/runner.py`) wiring the
adaptive poll queue, the daily sweep, and the digest onto one `asyncio` loop, per the
task-4 brief and its binding addendum.

Two behaviors the brief's sketch did not get right once `daemon_state` (task 1) entered
the picture. First, `Daemon.run(max_iterations=N)` returning because the budget ran out
is not the same event as a real stop: only `stop_event.is_set()` or
`daemon_state.stop_requested()` calls `mark_stopped()`. A bounded run (used by tests and
by `wfm scan --once`) leaves `status="running"` so a caller can start another bounded run
right after, and the per-iteration `heartbeat()` write stays the one place status flips
back to "running" after a poll. Second, `run()` checks `stop_requested()` before calling
`mark_started()`, not after: `mark_started()` unconditionally resets `status` to
`"running"` (by design, to clear a stale flag from a crashed process), so checking after
would silently clear a stop request nobody has acted on yet. A pending stop now short
circuits straight to `mark_stopped()` without ever claiming the daemon started.

Also split `analysis_service.analyze_item` into a dict-only wrapper around a new
`analyze_item_records(...) -> tuple[dict, list[Signal]]`, per the addendum: the daemon
needs the persisted `Signal` objects (with their DB-assigned `id`) to pass straight to
`alert_service.deliver`, and re-querying `ctx.signals.query(slug=...)` for them (the
brief's original approach) can return signals from a different rank or an earlier poll.

**Alternatives:** Calling `mark_stopped()` unconditionally at loop exit, matching the
brief literally (rejected: fails the brief's own heartbeat test once `daemon_state` is
real, since two bounded `run()` calls in the same test would otherwise show "stopped"
between them). Checking `stop_requested()` only inside the loop body, after
`mark_started()` (rejected: a stop requested between process start and the first
`Daemon(ctx).run()` call would be silently cleared).

## 2026-09-03 - Phase 7 tasks 3 and 4: the pin decay bound, the poll loop's own budget reservation, and the stop flag

**Context:** Three decisions taken on the user's behalf while reviewing the due queue
(task 3) and the daemon runner (task 4), each one against what the plan and its task
briefs actually said.

The first: the unchanged-poll decay lengthens an item's interval every time its book comes
back identical, and it did so without reference to `pin_weight`. A pinned item whose book
goes quiet was therefore dragged all the way to the 30 minute floor, so the first move on
it would be seen up to 30 minutes late. That is the exact case a pin exists for. It is
inherited from tasks 2 and 3, but task 4 is the first task that can observe it.

The second: the plan's rate constraint reads "the scheduler shortens intervals only with
budget the sweep has not reserved". Task 4 shipped `reserve`/`release`/`remaining_for` with
the sweep as the only production caller, so `remaining_for` never saw poll pressure and the
sweep's slack gate reduced to a constant that is always true.

The third: the per-iteration heartbeat added in a fix round wrote `status="running"`
unconditionally, erasing a pending `status='stopping'` before the loop top could read it.
The DB flag is the only stop mechanism on Windows, where `os.kill` calls
`TerminateProcess`, so `wfm daemon stop` silently stopped working. The fix round's suite
stayed green because no test set the flag mid-run.

**Decision:** Bound the decay by pin weight in `scheduler.py` (a
`PIN_DECAY_CAP_MULTIPLIER`, currently 4.0), leaving the unpinned branch byte-identical and
the 2 minute ceiling untouched, rather than deferring it to the live soak. Make the poll
loop reserve and release its own budget so the slack gate reflects real pressure. Fix the
stop flag at the repo layer, `status=CASE WHEN status='stopping' THEN status ELSE ? END`
on the `running` branch only, so every future heartbeat caller inherits the guard, with a
mid-run flag-stop regression test on both the idle and the poll path.

The pin multiplier is a judgement call, not derived from the spec, and is a candidate for
calibration in the phase 7 live soak. The reservation is advisory: the single `TokenBucket`
is what actually enforces the rate, so a too-permissive gate costs poll latency, not
compliance. At real catalog magnitudes (3839 items against 10080 requests/hour at the
default 2.8 req/s) the gate is still effectively always true, and the scheduler still never
consults `remaining_for`; closing that is left to task 5.

**Alternatives:** Deferring the pin decay to the live soak (rejected: task 6's simulated
day and the task 7 soak both read pin behaviour, so deferring means calibrating against a
curve already known to be wrong). Leaving the budget reservation with only the sweep as a
caller (rejected: a rate constraint that never constrains is decoration, and task 5 wires
the CLI on top of it). A blanket stop-preserving guard across the whole `heartbeat` method
(rejected: `mark_stopped` routes through `heartbeat`, so a blanket guard breaks it; a
`halted` transition overriding a pending `stopping` loses the label, not the stop).

## 2026-09-04 - Phase 7: daemon and adaptive scheduler

**Context:** Phase 7 replaces the phase 3 idea of a scheduled `wfm sync` invocation with a
long-running process that owns the poll loop, the daily sweep and the digest send, and
that has to survive being killed, sleeping overnight, and running on Windows where POSIX
signals are not a reliable stop mechanism.

**Decision:** One `asyncio` event loop (`wfm/daemon/runner.py::Daemon.run`) carries the
poll loop, the daily sweep and the 09:00 digest, rather than three concurrent `asyncio`
tasks each owning a slice of the work. At `concurrency = 1` nothing in this process is
ever genuinely parallel, so three tasks would buy nothing but a second layer of locking
around the queue, the budget and the circuit breaker, all of which a single loop body can
touch directly in sequence. The loop body checks each piece of daily work in a fixed order
every iteration: digest, then sweep (gated on budget slack via `_queue_has_slack`), then
the poll queue.

The sweep and digest each fire once per day under an "hour arrived and not run today"
rule rather than a fixed-offset timer: `_sweep_due`/`_digest_due` check `now.hour >=
config.sweep_hour` (or `digest_hour`) and that `daemon_state.daily_done(name)` is not
already today's date. `daemon_state.last_sweep_date`/`last_digest_date` persist which day
each last ran, so a restart mid-morning does not re-fire a sweep that already completed
today, and a daemon that was offline through its configured hour still catches up the
moment it comes back up, rather than waiting for the exact hour to roll around again on a
clock that may have moved past it. `mark_daily_done` writes the date only, never touching
`heartbeat_at`: liveness has exactly one source, the per-iteration heartbeat, so a wedged
loop cannot look alive just because the daily sweep wrote a row hours ago.

An item whose order book comes back byte-identical to its last poll doubles its interval
each further unchanged poll (`PollQueue.reschedule`, `decayed = interval * (2 **
decay_steps)`, capped at the floor), so a quiet book is polled less and less often rather
than at its full earned cadence forever. A pinned item's decay is capped at
`PIN_DECAY_CAP_MULTIPLIER` (4.0) times its own scored interval instead of running all the
way to the 30 minute floor: a pin means "tell me the moment this moves", and decaying it
like an unpinned item would detect the first move on it up to a floor interval late,
which is the exact case a pin exists for.

The priority score's volume term is `log1p(volume) / 10.0`, not volume itself. Raw
median-30d volume spans orders of magnitude across the catalog (a handful of trades a day
for a niche mod, hundreds for a meta one), so a linear term would let volume alone
saturate the score and drown out volatility, spread and pin for anything even moderately
liquid. The log compresses that range so all four weighted terms stay comparable in
practice, at the plan's chosen constant.

`poll_state` persists `(slug, rank) -> last_polled_at, due_at, interval_minutes,
unchanged_polls` (task 1's `m0003`), and `PollQueue.rebuild()` restores wall-clock
`due_at` from it on every start rather than treating a fresh process as a blank schedule.
A sleep or crash can leave hundreds of items simultaneously overdue; rebuild sorts the
restored overdue set oldest-due-first, releases up to `catchup_max_items` (25) of them as
due immediately, and spreads the remainder across the floor interval instead of firing
them all in the same iteration. The budget would serialize a full release anyway, so
letting it all through as "due now" would only make the loop look wedged rather than
actually catching up any faster.

`wfm daemon stop` requests a stop by writing `daemon_state.status = 'stopping'` rather
than calling `os.kill`. On Windows, anything short of `CTRL_C_EVENT`/`CTRL_BREAK_EVENT`
calls `TerminateProcess`, which has no graceful path: the in-flight poll is lost, the pid
file is orphaned, and status is stuck reading "running" forever. The database flag is
read identically by the loop on Windows and POSIX (`Daemon.run` checks it every
iteration, and `_sleep_until_next` checks it every chunk of an idle wait so a stop is
noticed within `MAX_SLEEP_CHUNK_S`, not after a full 30 minute sleep), so the same
mechanism works everywhere with no platform branch in the stop path itself. POSIX signal
handlers stay wired in `daemon_service.start` as a second path (useful for `Ctrl+C` on a
foreground run), and Windows falls back to `signal.signal` for a real `SIGTERM` where the
event loop's own handler registration is unavailable, but the flag is what every restart-
survivable stop actually goes through.

**Alternatives:** Three `asyncio` tasks coordinating over shared queue/budget/breaker
state (rejected: real concurrency risk for zero real parallelism at `concurrency = 1`,
and the phase 2 `FakeClock` cannot model true concurrency anyway, so tests of ordering
between tasks would not be trustworthy). A fixed-offset timer for the sweep and digest,
firing exactly at `sweep_hour`/`digest_hour` with no "already ran today" check (rejected:
loses catch-up after downtime and cannot tell a restart from a fresh day). Decaying every
unchanged item straight to the floor regardless of pin (rejected: defeats the purpose of
pinning). A linear volume term in the score (rejected: lets one input dominate and drown
out the other three). Releasing every overdue item as due-now after a restart (rejected:
the budget serializes it anyway, so the only effect is a loop that looks stalled).
`os.kill`/`SIGTERM` as the sole stop mechanism (rejected: not graceful on Windows, which
is this project's primary platform).
