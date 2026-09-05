# Warframe Market Stalker: Phase 8 (GUI) Design

**Date:** 2026-09-05
**Status:** Approved, pending implementation plan

## Purpose

Build the second frontend the phase 1 design reserved room for: a full click-through web
dashboard over `wfm.services`, replacing manual `wfm report`/`wfm scan`/`wfm signals` CLI
calls with a live, browsing-friendly view of the watchlist, item detail, signals feed,
groups, daemon status, and ledger/P&L.

## Scope

In: watchlist browser, item detail (price history chart, live rates), live signals feed
(WebSocket push), group management with a per-member signal roll-up, a real group-scoped
arbitrage analyzer (parts-vs-set), a daemon control panel, a ledger/P&L view.

Out: rivens (already filtered at catalog sync, phase 1). Any change to the read-only
non-goal — the GUI never places, edits or cancels an order, same as the CLI. Phase 9
(distributed operation) is a separate brainstorm; this design assumes one local SQLite
file as phases 1-7 do.

## Why not the workspace's usual GUI stack

Every other GUI tool in this workspace is PySide6/PyQt6. This one is FastAPI + Svelte
(Vite build) + WebSocket instead, because the ask is a "snappy" market analysis engine:
fast filter/sort/compare over the watchlist, live-updating charts, push-driven signal
feed. That wants real client-side reactive state more than it wants stack consistency
with the other tools here. `lightweight-charts` (TradingView) is the planned charting
library for price history. Server-rendered HTMX was considered and rejected for the same
reason: the interaction model (filter/sort/compare, live push) is what a thick client
does well and a thin one fights.

## Process model: the server runs inside the daemon

`wfm daemon start` becomes one `asyncio` event loop running two tasks concurrently:
`Daemon.run()` (unchanged) and a `uvicorn.Server.serve()` hosting the FastAPI app, both
built against the same `AppContext` instance.

This is not just a packaging preference. `wfm/api/ratelimit.py`'s `TokenBucket` is pure
in-memory per-process state with no cross-process coordination, and the
`DECISIONS.md` "request priority classes over one shared bucket" design (2026-08-27) only
holds if the GUI's `INTERACTIVE`-priority requests and the daemon's `BACKGROUND`/`BULK`
requests share one `Budget`/`TokenBucket` instance. Two separate processes each
independently rate-limiting to 2.8 req/s could combine to ~5.6 req/s against the real
2.8 req/s ceiling — a compliance breach, not just a slowdown. Running both inside one
process closes that gap by construction rather than by a coordination protocol.

**Consequence, accepted:** stopping the daemon from the GUI kills the process serving the
GUI. `wfm daemon stop` (CLI or GUI button) still works exactly as it does today — the
page just goes dark rather than showing a clean "stopped" state, since there is no
process left to serve that state. The GUI's stop control should warn ("this will
disconnect the GUI") before sending the request, but does not need to work around the
disconnect itself. A future session may reach for a small outer supervisor process if
this proves annoying in practice; not built now (YAGNI).

Every GUI HTTP endpoint is a thin wrapper over the existing `wfm.services.*` functions at
`Priority.INTERACTIVE`, enforced by the same import-linting architecture test that
already forbids `cli/` from reaching `store`/`api`/`analyzers` directly (2026-08-27
DECISIONS.md, "A services layer, enforced by a test") — extended to also cover the new
GUI package.

## Live signals feed

An in-process broadcaster (one queue per connected WebSocket client) that the poll loop
pushes newly-persisted signals into at the same point it already calls
`alert_service.deliver` in `Daemon.poll_once`. Event-driven push, not client polling —
the signals a client sees arrive the moment the poll loop produces them, matching the
"snappy" requirement above.

## Error handling

Service-layer exceptions (`ApiError`, `CircuitOpen`, and the `catalog_service.resolve()`
`LookupError`/`ValueError` cases for an ambiguous or missing item) map to clean JSON error
responses at the FastAPI layer — never a raw traceback. This also closes, for the GUI
path at least, the long-open "no top-level `ApiError`/`CircuitOpen` handler" gap tracked
since phase 3 (`main()` still dumps a traceback on a CLI network blip; unaffected by this
phase).

## Scope for the first build

- **Watchlist browser** — list, filter/sort, add/remove, same operations as `wfm watch`.
- **Item detail** — price history chart (`lightweight-charts`), current online rates,
  same data `wfm report --refresh` already assembles via `report_service`.
- **Live signals feed** — WebSocket push of new signals as the poll loop produces them.
- **Group management** — CRUD and membership over the existing `groups`/`group_members`
  tables (`group_service`), plus a per-member roll-up of each member's own item-level
  signals, plus the new arbitrage signal below where a group is shaped for it.
- **Daemon control panel** — status (running/halted/heartbeat/last sweep/last digest),
  stop (see the accepted consequence above).
- **Ledger/P&L view** — over `ledger_service` (`holdings`, `pnl`), read-only like the CLI.

## Group-level analysis: parts-vs-set arbitrage

`GroupAnalyzer` (`wfm/analyzers/base.py:47`) has been a protocol only since phase 5 — no
concrete group-scoped analyzer exists, only item-scoped ones (`flip`, `revert`,
`selltime`). Phase 8 implements the first one, rather than deferring it: a
`SetArbitrageAnalyzer` that detects mispricing between a bundled item ("Set") and the sum
of its component parts.

**Why this is the right first group signal:** Warframe Prime items sell both as
individual parts/blueprints and as a bundled "Set" listing (confirmed against the real
catalog: `mirage_prime_set` alongside `mirage_prime_blueprint`,
`mirage_prime_chassis_blueprint`, `mirage_prime_neuroptics_blueprint`,
`mirage_prime_systems_blueprint`; same shape for weapons, e.g. `tigris_prime_set` +
`tigris_prime_barrel`/`_blueprint`/`_receiver`/`_stock`). Nobody keeps these two markets
in sync, so the spread between them is a real, tradeable arbitrage that no per-item
signal (each of which only reasons about its own price history) could ever surface. A
per-member signal roll-up is not a substitute for this: it is a genuinely different
computation over the whole group at once, which is what `GroupAnalyzer.evaluate(fss,
ctx)` exists for.

**Identifying the Set within a group:** by slug suffix (`_set`), not a schema change. A
group is "arbitrage-shaped" when exactly one member's slug ends in `_set` and the rest
are its parts. This is purely a matter of how the user curates the group via the existing
`wfm group add` commands — no new CLI, no new column. A group with zero or 2+ `_set`
members simply produces no signal from this analyzer (silent no-op, not an error).

**The two possible signals**, using each member's live `online_best_ask`/
`online_best_bid` (`FeatureSet.book`, the same live book data `flip` already reads):

- **Buy the Set, part it out.** Profitable when `set_ask < sum(parts_bid)`.
  Margin = `sum(parts_bid) - set_ask`.
- **Buy the parts, sell as a Set.** Profitable when `sum(parts_ask) < set_bid`.
  Margin = `set_bid - sum(parts_ask)`.

Both directions are checked independently; a missing price on any one leg (a part with no
online sellers) skips only the check that needed it, rather than erroring the whole
evaluation.

**Analyzer shape**, mirroring `flip.py`:

- `name = "set_arbitrage"`, `horizon = Horizon.URGENT`, `scope = Scope.GROUP`.
- `required_features() -> {"book"}`.
- `DEFAULTS` mirrors `flip`'s shape: `min_margin_plat`, `min_margin_pct`, an
  `expiry_minutes` (arbitrage windows close fast, same reasoning as `flip`'s 20 minutes).
- Confidence formula in the same style as `flip`: scaled off how far the margin clears
  its threshold.
- The `Signal` row attaches to the **Set's** `(slug, rank)` — the schema requires exactly
  one slug per signal, and the bundle is the natural anchor since it is the one
  listing whose mispricing is being reported. `Direction.BUY` means "buy the set, part it
  out" (the set itself is cheap); `Direction.SELL` means "buy parts, sell as a set" (the
  set is expensive relative to its parts) — the same BUY=cheap/SELL=expensive convention
  `revert` already uses for a non-owned tradeable. Evidence carries every leg's ask/bid
  and which check triggered, so the direction label's meaning is never ambiguous to a
  reader of `wfm signals`/the GUI regardless of which leg fired.
- **Known limitation, explicitly out of scope for v1:** assumes each part is needed at
  quantity 1 to assemble one Set. True for every Prime Warframe/weapon set structure
  checked against the real catalog. No quantity field exists on group membership, and
  this design does not add one. A future group with a part that needs quantity > 1 would
  silently under-count the parts-side cost; if that ever turns out to matter, it is a
  small, isolated follow-up (an optional per-member quantity column), not a redesign.

## Testing

Backend GUI tests are thin: FastAPI's `TestClient`/WebSocket test client checking
routing, error-mapping and serialization only. The actual logic (services layer,
`SetArbitrageAnalyzer`) is covered where it already lives — the services and analyzer
test suites — not re-tested through the HTTP layer. `SetArbitrageAnalyzer` itself gets a
normal analyzer unit-test suite (both arbitrage directions, the "not arbitrage-shaped"
no-op case, each missing-leg guard), same TDD discipline as every other analyzer.

Svelte frontend testing is kept to type-checking (`svelte-check`) rather than a full
component-test suite, given this is a personal tool. Revisit if the frontend grows past
what type-checking alone can catch.

## Deferred, not part of this phase

- A small outer supervisor process for a cleaner GUI-stop story (see "Process model"
  above) — only worth building if the current "page goes dark" behavior turns out to be
  annoying in practice.
- Per-member part quantity on group membership (see the arbitrage analyzer's known
  limitation above) — only worth building if a real group needs it.
- Phase 9 (distributed operation) — separate brainstorm, not touched by this design.
