# Warframe Market Stalker: Design

**Date:** 2026-08-27
**Status:** Approved, pending implementation plan

## Purpose

Track warframe.market prices for a user-selected watchlist, backfill real trade history,
and produce buy/sell timing signals across three horizons. Replaces the single-file
`maket_hunter.py` prototype.

## Scope

In: catalog sync, full-catalog daily statistics backfill, watchlist order-book polling, a
feature layer, three pluggable analyzers, a trade ledger, terminal and Discord alerting, a
CLI, a long-running daemon.

Out: rivens (filtered at catalog sync), a GUI dashboard (later phase), any platform other
than PC crossplay.

**Non-goal, permanently:** automated trading. This tool reads and advises. It never
places, edits or cancels an order. Warframe.market's rules declare trade bots a grey area
with stricter limits expected, so staying strictly read-only keeps the project on the safe
side of a line they have said they may move.

**Planned second frontend:** a full GUI where items are clicked through, current rates and
history are shown, and items are analyzed individually and in user-defined groups. Not
built in this phase, but the architecture below is constrained so it is additive rather
than a rewrite: a `services/` layer both frontends call, a scope-aware analyzer registry
that admits group analyzers, persisted item groups, and an interactive request priority.

## Confirmed requirements

- User-selected watchlist. 30 minutes is a per-item polling floor, with adaptive upscaling
  spending spare API capacity on items that warrant it.
- SQLite.
- No rivens.
- CLI and alerts first. Dashboard later, as a second frontend over the same services.
- Self-explaining code, comments only for non-obvious why, heavy modularization.
- Tests written alongside each unit.
- Must not get rate-limited or blocked.

## Verified API findings (probe, 2026-08-27)

- v2 `/orders/item/{slug}` returns the full order book (731 orders on archon_continuity)
  with platinum, quantity, rank, visible, timestamps, and user status (online / ingame /
  offline). `/top` returns only 5 per side.
- v1 `/items/{slug}/statistics` works and is the key unlock: 90 days of daily candles plus
  48 hours of hourly candles, from real closed trades, with volume, OHLC, median, moving
  average and Donchian bands, split by mod rank.
- v2 `/versions` exposes collection version tokens for cheap catalog change detection.
- v2 has no statistics endpoint (404). Catalog is 3,745 items.
- 12 requests in 2.6 s drew no throttle.

## Published rules (https://docs.warframe.market/docs/rules/overview/, read 2026-08-27)

Binding constraints, not suggestions. The design complies with each.

- General public API limit is **3 requests per second**. Contract search endpoints are
  stricter at roughly 10 to 20 per minute; this project does not use them. Limits "may
  change without notice".
- A **dedicated, descriptive User-Agent** is required, identifying project name, version,
  and a contact or repository. Their example: `ExampleMarketTool/1.2.0
  (+https://example.com/contact)`. Disguising the client as a browser is prohibited.
- Applications must "minimize unnecessary API calls", use caching, reuse responses, avoid
  tight polling loops, and "prefer incremental updates or WebSocket subscriptions when
  appropriate".
- "Do not repeatedly fetch large collections or high-traffic endpoints when local caching
  would work."
- Website clones, data mirrors and traffic offloading layers are prohibited. This project
  is a private analysis client and is none of those.
- Automation and trade bots are "a grey area" with stricter rules expected. See the
  read-only non-goal above.
- They may restrict individuals, IPs, networks and regions without notice.

At 3 req/s a full 3,745-item sweep is ~21 minutes.

## Architecture

### Package layout

```
wfm/
  cli/          argparse subcommands, one module each, no business logic
  services/     every use case, the only layer a frontend is allowed to call
  api/          client.py, ratelimit.py, endpoints.py
  store/        schema.py, migrations/, one repository per table
  sync/         catalog.py, backfill.py, scheduler.py, budget.py
  features/     price.py, book.py, seasonality.py, market.py
  analyzers/    base.py, registry.py, flip.py, revert.py, selltime.py
  alerts/       base.py, terminal.py, discord.py, digest.py
  ledger/       trades.py, holdings.py, pnl.py
tests/          mirrors the source tree
```

Two dependency rules, both load-bearing.

**Frontends call `services`, nothing else.** Every use case (resolve an item, fetch its
current rates, run analyzers over it, add to watchlist, record a trade, build a report)
lives in `services/` and returns plain data structures. `cli/` is a thin argparse shell
over it. The planned GUI is a second equally thin shell. No `store`, `api` or `analyzers`
import ever appears in a frontend module. This is what makes the GUI additive rather than
a rewrite, and it is enforced by an import-linting test, not by good intentions.

**`analyzers` import neither `api` nor `store`.** They take FeatureSets and return
Signals. That is what keeps analyzer tests free of network and database.

### Storage

SQLite in WAL mode. Every price-bearing table keys on `(slug, rank)`. `rank` is `0` for
rankless items so the key is never null.

- `items`: slug PK, name, tags, url_name, max_rank, ducats, is_set, canonical_rank,
  last_seen_version. Rivens excluded at sync.
- `daily_stats`: `(slug, rank, date)` PK. volume, open, high, low, close, median, avg,
  wa_price, moving_avg, donch_top, donch_bot. Sourced from v1 statistics. Roughly 340k
  rows at full-catalog steady state.
- `hourly_stats`: same key at hour granularity. 48-hour rolling window from v1, pruned
  beyond ~14 days.
- `order_snapshots`: `(slug, rank, ts)`. Watchlist only. Aggregates, not individual orders:
  best_bid, best_ask, online_best_bid, online_best_ask, bid and ask depth at five price
  levels, order counts by status, spread.
- `order_snapshots_raw`: sampled 1-in-N raw payloads for debugging only. This replaces the
  prototype's per-row raw JSON, which cost 380 KB for 59 rows.
- `signals`: id, slug, rank, analyzer, ts, direction, magnitude, confidence, evidence_json,
  alerted_at, expires_at.
- `trades`: id, slug, rank, ts, side, quantity, platinum, note.
- `watchlist`: slug, rank, added_at, pin_weight, alert_override.
- `groups`: id, name, created_at. `group_members`: group_id, slug, rank. Named
  user-defined collections, so a group built in the CLI is the same group the GUI renders,
  and group-scoped analysis is available to both. Group membership is independent of the
  watchlist: a group may contain unwatched items, which are analyzed from `daily_stats`
  without an order book.
- `sweep_state`: resumable checkpoint for the catalog and backfill sweeps.
- `features`: `(slug, rank, ts)`. Debug and test only, written only under
  `--persist-features`. Nothing in the production path reads it.

Holdings and P&L are views over `trades`, never stored.

Indexing: `daily_stats` clustered on `(slug, rank, date)`, since every analyzer reads
per-item windows.

### API client and rate safety

One process-global `TokenBucket`. No code path constructs an HTTP request except through
the client that holds it, so the limit cannot be bypassed.

- **Hard ceiling 3.0 req/s**, the documented public limit, not configurable above it.
  **Default sustained 2.8 req/s.** The 0.2 headroom exists because the server's rate
  window and our clock will not agree, and sitting exactly on a documented limit is how a
  compliant client still trips it. Continuous refill, no bursts.
- Concurrency 1. Serial requests, which also makes budget arithmetic exact.
- User-Agent in the documented form, `WFMStalker/<version> (+<repo or contact>)`. Set in
  one place, asserted by a test, and never spoofing a browser.
- Accept-Encoding gzip.
- 429: honor Retry-After exactly when present. Absent, exponential backoff from 2 s,
  doubling, capped at 5 minutes.
- Circuit breaker: three consecutive 429s or five consecutive 5xx trips it. It stops the
  sweep, writes the reason to `sweep_state`, and exits nonzero rather than retrying into a
  block. A cooldown must elapse before any component may request again.
- Backoff is global, not per-endpoint. A 429 anywhere pauses everything.
- Conditional requests via ETag / If-None-Match. The v2 `/versions` token gates catalog
  sync, so an unchanged catalog costs one request.
- Retries only on 429, 5xx and connection errors. Never on other 4xx.

### Why the full-catalog sweep complies

The rules forbid "repeatedly fetching large collections when local caching would work",
which is aimed at exactly this shape of request. The sweep is built to satisfy that rule
rather than skirt it:

- The 90-day history seed runs **once per item, ever**. It is never re-fetched.
- Daily runs request only candles newer than what is stored, which is the "prefer
  incremental updates" instruction.
- The catalog itself is gated on the v2 `/versions` token, so an unchanged catalog costs
  one request rather than 3,745.
- Everything is cached locally in SQLite and read from there. The API is never queried for
  data already on disk.
- One pass per day at 2.8 req/s, in a low window, is not a tight polling loop.

If the maintainers later signal that daily full-catalog statistics are unwelcome, the
fallback is watchlist-only refresh plus a much less frequent catalog pass. The sweep is a
single module so that swap is contained.

### Budget

The scheduler cannot issue a request. It asks `sync/budget.py` for a grant. The budget
knows the day's allowance, what the daily sweep has reserved, and what each consumer has
spent. The sweep reserves its window first, and the adaptive loop is only ever offered
what remains. This is what prevents adaptive upscaling from becoming unbounded polling.

Grants are issued in three priority classes against one shared bucket, so priority changes
ordering and never the rate:

- **INTERACTIVE**: a user is waiting. A CLI command, or a GUI click on an item whose rates
  are not cached. Preempts the queue, served next.
- **BACKGROUND**: the adaptive watchlist poll loop.
- **BULK**: the daily sweep, which yields to both.

Interactive grants are additionally capped per minute, so holding down a mouse button in
the future GUI cannot starve the background loop or push the client toward the limit.

### Daemon and scheduler

`wfm daemon` is a single-threaded asyncio loop. Serial requests mean no thread pool, and
one loop keeps budget, breaker and scheduler state in one place without locking. It writes
a PID file and a heartbeat row for `wfm daemon status`.

Three cooperating tasks:

1. Catalog and backfill sweep, daily in a configured low window (default 04:00 local).
   Checks `/versions`, syncs `items` only if the token moved, walks daily stats for all
   items, checkpoints after each into `sweep_state`. A restart resumes.
2. Watchlist poll loop, continuous. Pops the next due item, spends one grant, writes an
   `order_snapshots` row, computes features in memory, runs analyzers, emits signals.
3. Digest timer, fires 09:00 local, drains undelivered DAILY signals into one Discord
   message.

Priority scoring. Each watchlist item carries `due_at`, baseline `now + 30 min`. The
interval shortens by:

```
score = w_vol*recent_volatility + w_liq*volume + w_spread*online_spread + w_pin*pin_weight
```

Score maps to an interval between 2 and 30 minutes. All weights live in config. The queue
is a heap on `due_at`. Two guards: an item whose book does not change across several
consecutive polls has its interval decayed back toward 30 regardless of score, and an
exhausted budget makes the loop sleep rather than slip below the floor.

Failure behavior: a tripped breaker pauses all three tasks and emits an operational alert.
Unclean shutdown costs at most one in-flight poll. The queue rebuilds from `watchlist` on
start.

### Feature layer

Four modules of pure functions. No database access, no network, no shared state. Computed
in memory per tick and handed to analyzers.

- `price.py`: rolling median and MAD over 7 / 30 / 90 day windows. Robust z-score using MAD
  rather than standard deviation, because plat prices have fat tails and outliers would
  otherwise inflate SD and suppress every signal. Percentile rank within window. ATR-style
  volatility from daily high and low. Volume trend as short-window over long-window ratio.
  Donchian position within the 90-day channel.
- `book.py`: best bid and ask, online-only best bid and ask (the only actionable ones), raw
  and online spread in absolute and percentage terms, depth at five levels per side, book
  imbalance as bid depth over total depth, and staleness as the share of orders whose
  seller has been offline beyond a threshold.
- `seasonality.py`: hour-of-week volume and price profile, 168 buckets, plus the current
  deviation from the bucket's expectation. Reports a confidence that scales with sample
  count per bucket. Analyzers must respect that confidence.
- `market.py`: cross-sectional. Market-wide median price change as a plat inflation proxy,
  per-tag aggregates, and each item's return relative to its tag cohort. This is what
  separates an item dropping from everything dropping.

A `FeatureSet` dataclass bundles these plus provenance: which windows were available, how
many samples backed each figure, and which fields are absent. Analyzers check availability
rather than assume, so a thin-history item produces no signal instead of a confident wrong
one.

### Analyzers

```python
class Analyzer(Protocol):
    name: str
    horizon: Horizon                 # URGENT | DAILY
    scope: Scope                     # ITEM | GROUP
    def required_features(self) -> set[str]: ...

class ItemAnalyzer(Analyzer):
    def evaluate(self, fs: FeatureSet, ctx: Context) -> list[Signal]: ...

class GroupAnalyzer(Analyzer):
    def evaluate(self, fss: list[FeatureSet], ctx: Context) -> list[Signal]: ...
```

`Context` carries ledger-derived holdings, watchlist config, and per-analyzer thresholds. A
registry maps name to class. Adding an analyzer is one file plus one registration line, and
config decides which are enabled. The runner checks `required_features` against the
FeatureSet and skips an analyzer rather than letting it fail on missing data.

The registry and runner are **scope-aware from the start**, though all three analyzers
shipped in this phase are ITEM-scoped. GROUP scope exists because the planned GUI analyzes
user-defined groups, and retrofitting a second evaluation shape into a registry that
assumes one is a refactor of every call site. Building it in costs a dispatch branch now.
Candidate group analyzers for later, none built here: cohort divergence within a group,
correlation clustering, and group-level portfolio exposure.

Every Signal carries `evidence`, the exact numbers that triggered it. That is what the
terminal prints and what makes a signal auditable, which matters because features are not
persisted in the production path.

**`flip.py`, URGENT.** Fires when the online ask is below the online bid, or below a robust
fair-value estimate by more than a threshold. Fair value is the rolling median, not the raw
best bid, so one lowball buy order cannot define the market. Guards: a minimum absolute
plat margin, so a 2p edge on a 15p item does not alert. Book depth must show the price is
real rather than one mispriced listing under a wall. Staleness must be low. Daily volume
must clear a floor, because a wide spread on an illiquid item is its normal state, not an
opportunity. Signals carry a short `expires_at` and are suppressed while the same
opportunity remains open.

**`revert.py`, DAILY.** Fires when the robust z-score crosses a threshold against the
90-day window, direction by sign. Requires the cohort-relative return from `market.py` to
agree, so a market-wide plat crash does not mark every item a buy. Requires volume trend
stable or recovering, since price falling on collapsing volume means the item is dying, not
cheap. Emits accumulate or distribute with magnitude scaled by z, not a binary call.

**`selltime.py`, DAILY.** Evaluates only items held per the ledger. Combines percentile
rank within the 90-day window, the seasonality read on the current and upcoming
hour-of-week buckets, and online bid depth, which determines whether a sale actually fills.
Output is list now, hold, or wait until a named window, plus unrealized P&L against cost
basis. Below the seasonality confidence gate it says so and falls back to percentile rank
alone rather than inventing a weekly rhythm from noise.

### Alerting

```python
class AlertSink(Protocol):
    def deliver(self, signals: list[Signal]) -> DeliveryResult: ...
```

`terminal.py` is the base sink, always enabled, no configuration. Every signal lands here.
The daemon prints as signals fire, and `wfm signals` renders the same formatter over the
`signals` table, so live and historical output come from one code path. Signals render with
their evidence.

`discord.py` is optional and mirrors a filtered subset. With no webhook configured the tool
is fully functional and never mentions Discord. Delivery failures are logged and never
propagate, since the terminal sink already holds the signal.

Routing follows horizon:

- URGENT: terminal immediately. Discord immediately if the signal clears per-analyzer
  confidence and magnitude thresholds, or the item carries `alert_override`.
- DAILY: terminal immediately, batched into the 09:00 digest for Discord, grouped by
  analyzer.
- Operational (breaker tripped, sweep failed): terminal always, Discord additionally if
  configured.

`signals.alerted_at` makes delivery idempotent. The digest drains by that column, so a
restart mid-digest cannot double-send or silently drop.

Noise control: a per-item per-analyzer cooldown, deduplication against open signals so a
still-valid flip does not re-fire each poll, and a digest cap reporting the top N by
magnitude plus a count of the rest.

### Ledger

`trades` records both sides. Holdings and realized P&L derive from it. Beyond P&L, the
ledger enables the validation harness to replay real fills against signal history and
report which analyzers earn their keep.

### CLI

One module per subcommand under `cli/`, parsing arguments and calling a service. No
business logic, per the frontend dependency rule above.

```
wfm sync
wfm backfill [--all|--slug X]
wfm search <query>
wfm watch add|rm|ls [--rank|--alert|--pin N]
wfm watch suggest [--top N]
wfm group new|rm|add|remove|ls <name> [items...]
wfm group show <name>
wfm scan [--once]
wfm daemon start|stop|status
wfm signals [--since|--analyzer|--slug]
wfm trade buy|sell <slug> <qty> <plat> [--rank]
wfm holdings
wfm pnl [--realized|--since]
wfm report <slug>|--group <name>
wfm validate [--analyzer X]
```

`search` fuzzy-matches the catalog and resolves to a slug, so slugs are never typed by
hand. Results group by tag, the same grouping the future GUI category browser will use.
`watch suggest` ranks candidates and requires confirmation. Nothing auto-adds.

`--rank` defaults to canonical everywhere, `--rank all` opens it up.

Global flags: `--json` on every read command, so output is scriptable and the future
dashboard consumes the same path. `--dry-run` on anything that writes or spends budget.
`--verbose` for request-level logging when diagnosing rate limits.

## Testing strategy

Tests are written alongside each unit, not after.

- Rate limiter: token bucket against a fake clock. Backoff and breaker against a stubbed
  transport returning scripted 429 and 5xx sequences. Budget against a fake clock and a
  synthetic watchlist, including priority ordering and the interactive per-minute cap.
- Compliance: a test asserting the configured rate cannot exceed 3.0 req/s however config
  is set, and a test asserting the User-Agent is present and matches the documented shape.
- Architecture: an import-linting test asserting no module under `cli/` imports `store`,
  `api` or `analyzers`. This is what keeps the frontend boundary real once the GUI lands.
- Scheduler: the loop takes a clock and a budget as constructor arguments, so a fake clock
  and stub client simulate a full day in milliseconds. Assert the 30-minute floor held, a
  hot item reached 2 minutes, a dead item decayed, and a starved budget degraded gracefully
  rather than stalling.
- Features: heaviest coverage, since the layer is pure and everything depends on it.
  Hand-computed fixtures per statistic. Explicit edge cases: empty window, single data
  point, all-identical prices where MAD is zero and the z-score must not divide by zero,
  gaps in the daily series, an item younger than its window.
- Analyzers: constructed FeatureSets in, expected Signals out. Every guard gets a test
  proving it suppresses what it exists to suppress: thin book kills the flip, market-wide
  drop kills the reversion buy, thin history kills the seasonality claim.
- Alerts: stub transport for formatting and for swallowed webhook failures. Routing tested
  as a pure function from signal to sink set. An explicit idempotency test: run the digest,
  simulate a crash mid-send, re-run, assert nothing double-sends and nothing is lost.
- Integration: one separate, manually-run test hitting the real API a handful of times to
  confirm endpoint contracts still hold.
- Validation harness: replays historical `daily_stats` through the analyzers and scores
  signals against subsequent price movement, to tune thresholds before trusting them.

## Build order

1. Store and schema, with migrations.
2. API client, rate limiter, budget. Nothing else is safe to build first.
3. Catalog sync and backfill. Run the full sweep, get real data on disk.
4. Features. Fully testable against backfilled data, no daemon needed.
5. Analyzers and the validation harness. Tune against real history before going live.
6. Alerts and ledger.
7. Daemon and scheduler last, since by then every piece it orchestrates is tested.

`services/` is not a phase. Each phase adds its use cases there and the CLI wires to them,
so the frontend boundary is exercised continuously rather than discovered at GUI time.

Phases 1 to 4 yield a working `report` command. Phase 5 yields signals validated against
history. The daemon is last because it is the only part that can get the client blocked,
and it should be the least uncertain code in the repo by the time it runs.

## Deferred, deliberately

- **GUI.** Brainstormed separately once the CLI works. Full click-through browsing of
  items, current rates and history, and analysis both per item and per group. The four
  structural accommodations above (services layer, scope-aware registry, persisted groups,
  interactive priority) exist so that work is additive.
- **WebSocket subscriptions.** The rules prefer them over polling, and they would cut
  request load while improving flip latency. A separate transport with its own failure
  modes, so it is an investigation after the polling path is proven, not a phase-2 gamble.
- **Group analyzers.** The protocol admits them now, none are implemented.

## Migration from the prototype

The existing 59 snapshots (6 slugs, 25 hours, `/top` only, no volume) are discarded. The
old database file is not migrated. The duplicated `maket_hunter.py` at the repo root and in
`wfm/` collapses into the new package.
