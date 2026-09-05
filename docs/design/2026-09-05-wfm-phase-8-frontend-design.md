# Warframe Market Stalker: Phase 8 Frontend Design

**Date:** 2026-09-05
**Status:** Approved, pending implementation plan
**Amends:** `docs/design/2026-09-05-wfm-phase-8-gui-design.md`

## Purpose

Phase 8's backend shipped 15 JSON endpoints and a WebSocket, with no frontend. The only
way to use it is Swagger at `/docs`, which is an API console, not a dashboard. This design
covers the frontend that makes the tool clickable: a dark, tabbed, single-page dashboard
served by the same FastAPI app, plus the three backend gaps that dashboard exposes.

## Amendment to the phase 8 GUI design: no Svelte

The approved design specified Svelte + Vite + `lightweight-charts`. This build uses a
single self-contained HTML file with vanilla JS instead, served as a static asset.

**Reasoning.** The Svelte decision was made to buy reactive client state for
filter/sort/compare and live-updating charts. That reasoning still holds in principle, but
it front-loads an npm toolchain, a build step and a `svelte-check` gate before anything is
on screen, for a personal single-user tool whose entire frontend fits in one file. Vanilla
gets a working dashboard immediately and stays editable in place with no build.

**What would trigger revisiting Svelte:** the page outgrowing roughly 1500 lines, or the
first time a piece of shared state has to sync across three or more tabs and the manual
wiring starts producing bugs. Neither is true for the six tabs below. Recorded here so the
deviation is deliberate and has an exit condition, rather than being forgotten.

`lightweight-charts` is kept, but vendored into `wfm/gui/static/` as a single file rather
than loaded from a CDN. Same absence of a build step, and the dashboard then works with no
internet, which matters because the daemon it monitors runs locally.

## Backend additions

Four gaps the dashboard cannot work around.

### 1. `GET /catalog`

The catalog browser needs to page through all 3839 items. `ItemsRepo.search()` is a
`LIKE` query hard-capped at 20 rows with no offset and no total count, built for
`wfm search`, not for browsing.

```
GET /catalog?q=<str>&limit=100&offset=0
-> {"total": 3839, "limit": 100, "offset": 0,
    "items": [{"slug", "name", "tags", "max_rank", "canonical_rank", "is_set"}, ...]}
```

- `ItemsRepo.page(query: str | None, limit: int, offset: int) -> list[Item]`, ordered by
  name, reusing the existing `_escape_like` helper.
- `ItemsRepo.count(query: str | None = None) -> int`, extending the current no-argument
  `count()` so the same filter produces the total the pager needs.
- `catalog_service.browse(ctx, q=None, limit=100, offset=0) -> dict`, reusing
  `catalog_service._as_dict` for each row.
- `wfm/gui/routes/catalog.py`, a thin route, consistent with every other GUI route.

Filtering and paging stay in SQL. 3839 rows is small enough to ship whole, but doing so on
every keystroke is wasteful and the pattern would not survive a larger catalog.

`limit` is clamped server-side (max 500) so a hand-edited URL cannot ask for the whole
catalog in one response.

### 2. `GET /signals`

The Signals tab is blank on load without it. The WebSocket at `/ws/signals` pushes signals
as the poll loop produces them, which means a freshly opened page shows nothing until the
next poll happens to fire, potentially half an hour later.

```
GET /signals?limit=50&since=<iso8601>&analyzer=<str>&slug=<str>
-> [ {"id", "slug", "rank", "analyzer", "ts", "horizon", "direction",
      "magnitude", "confidence", "evidence", "alerted_at"}, ... ]
```

A thin wrapper over the existing `alert_service.list_signals`, whose signature already
takes exactly these filters. New module `wfm/gui/routes/signals.py`; the WebSocket stays
in `signals_ws.py` untouched.

The dashboard uses this for the initial load and the WebSocket for everything after,
appending pushed signals to the list it already has.

### 3. `GET /items/{slug}/history`

The price history chart has no data source without it. `report_service.report()` returns
`FeatureSet.to_dict()`, which carries only aggregated statistics (`median_90d`,
`robust_z`, `last_close`, and so on). The daily candle series itself is never in that
payload. `DailyStatsRepo.window()` has exactly the OHLC and volume the chart needs, but
`wfm/gui` may not import `wfm.store` (enforced by `tests/test_architecture.py`) and no
service function exposes raw candles today.

```
GET /items/{slug}/history?rank=<int>&days=90
-> [ {"date", "open", "high", "low", "close", "volume"}, ... ]
```

- `report_service.history(ctx, slug_query, rank=None, days=90) -> list[dict]`, resolving
  the slug through `catalog_service.resolve` the way `report()` does, so the same name or
  slug the rest of the API accepts works here too, then reading `ctx.daily.window()`.
- A route on the existing `wfm/gui/routes/items.py`.

`days` is clamped server-side (max 365). Rank defaults to the item's `canonical_rank`, via
the same resolution path `report()` uses, so the chart and the statistics beside it are
always describing the same series.

**Anchoring.** `DailyStatsRepo.window(end=None)` defaults its window to today UTC, but the
newest candle is always the previous complete day, because these come from the API's
`statistics_closed`. Phase 4's review fixed exactly this class of off-by-one for the
feature windows by anchoring on the newest complete day, and the shared `anchor_date`
helper (promoted out of `feature_service` in phase 7 task 0b) is what the rest of the
codebase uses. `history()` anchors the same way rather than passing `end=None`, so a 90
day request returns 90 days of candles rather than 89 and a gap.

### 4. Static file serving

```python
app.mount("/static", StaticFiles(directory=<pkg>/gui/static), name="static")

@app.get("/", include_in_schema=False)
async def index() -> FileResponse: ...
```

An explicit `GET /` plus a prefixed `/static` mount, deliberately **not** a catch-all
`StaticFiles(html=True)` mounted at `/`, which would shadow the API routers depending on
registration order. The static directory is resolved from the package location
(`Path(__file__).parent / "static"`), not the process working directory, so the dashboard
works regardless of where `wfm daemon start` was run from.

### Market context caching

`report_service.report()` calls `feature_service.market_context()` on every invocation,
which is a sampled pass across the whole catalog. `report_group` already avoids paying
this per member (it builds one context for the group), and the item detail panel has the
same problem in a worse shape: browsing the catalog means one full sampled pass per click.

The GUI caches one `MarketContext` on `app.state`, rebuilt when older than 15 minutes. It
is a market-wide figure that moves slowly, which is the same justification `report_group`
already records for building it once. `GET /items/{slug}` passes the cached instance
through `report()`'s existing `market` parameter, which exists for precisely this.

The cache is GUI-local. The daemon's poll loop keeps its own, unchanged.

**Note, not addressed here:** `report()` also calls `feature_service.persist()`, so a GET
of item detail writes a feature row when `config.persist_features` is on (it defaults to
off). This predates the frontend and is out of scope, but it means item detail is not
strictly read-only under a non-default config. Recorded so a later reader is not surprised.

## Layout

```
┌────────────┬──────────────────────────────────────────────┬─────────────────┐
│ WFM        │  Catalog                          [search…]  │  Mirage Prime   │
│            │                                              │  Set  rank 0    │
│ ▸ Catalog  │  Name              Tags        Rank   Last   │  ─────────────  │
│   Watchlist│  ─────────────────────────────────────────── │  ╭───────────╮  │
│   Signals  │  Mirage Prime Set  set,frame    0     105p   │  │  price    │  │
│   Groups   │  Mirage Prime Bp   part,frame   0      38p   │  │  history  │  │
│   Daemon   │  Mirage Prime Chas part,frame   0      22p   │  ╰───────────╯  │
│   Ledger   │  Mirage Systems Bp part,frame   0      19p   │  median90  105  │
│            │  …                                           │  robust z −0.71 │
│ ● daemon   │                                              │  pct90     0.13 │
│   running  │  ‹ 1 2 3 … 39 ›            3839 items        │  bid 67 ask 95  │
│            │                                              │  [Refresh live] │
│            │                                              │  [+ Watchlist]  │
└────────────┴──────────────────────────────────────────────┴─────────────────┘
```

Left rail of tabs, centre list pane, right detail panel. The detail panel is shared: it is
how you look at one item, reached from either Catalog or Watchlist, and it is not a tab of
its own because you never arrive at it except from a list.

Daemon status is pinned to the bottom of the rail, visible from every tab, because "is the
thing that collects my data actually alive" is the question you want answered without
navigating.

## Tabs

Organised by what each one is for, not by which endpoint backs it.

| Tab | Purpose | Backed by |
|---|---|---|
| **Catalog** | Look anything up. Paged, searchable, all 3839 items. | `GET /catalog`, `GET /items/{slug}` |
| **Watchlist** | What the daemon is actively polling. Add, remove, see pin and alert flags. | `GET/POST /watchlist`, `DELETE /watchlist/{slug}/{rank}` |
| **Signals** | What the analyzers found. History on load, live push after. | `GET /signals`, `WS /ws/signals` |
| **Groups** | Curated sets, membership, the parts-vs-set arbitrage roll-up. | `GET/POST /groups`, `.../members`, `GET /groups/{name}/analysis` |
| **Daemon** | Is it running, when did it last beat, last sweep, last digest. Stop. | `GET /daemon/status`, `POST /daemon/stop` |
| **Ledger** | What you hold and what you made. | `GET /ledger/holdings`, `GET /ledger/pnl` |

## Item detail panel

Header: name, slug, rank, tags, whether it is a set, whether it is watched
(`report()` already returns `watched`).

**Price history chart.** `lightweight-charts`, fed from `GET /items/{slug}/history`. This
is a second request alongside the report, deliberately: the statistics and the candles come
from different shapes of data and the panel renders each as it arrives rather than blocking
on both.

**Statistics.** The figures `report_service.report()` already assembles: `median_90d`,
`robust_z`, `percentile_90d`, `atr_14d`, Donchian position, volume trend, and the market
block's `excess_return_7d` where a cohort exists.

**Book.** Last stored online bid/ask, spread, depth, and `book_age_seconds` rendered as a
human staleness ("4 hours old"). A `[Refresh live book]` button calls
`GET /items/{slug}?refresh=true`, the single INTERACTIVE request, and re-renders.

**Actions.** Add to or remove from the watchlist.

### Rendering nulls honestly

Phase 4's review established that this codebase never invents a number: a thin-history
item legitimately returns `None` for most windows, and the report payload carries
provenance sample counts explaining why. The dashboard must preserve that. A `None`
statistic renders as an explicit unavailable state naming the reason
(`"unavailable (3 candles)"`), never as a blank cell, a dash, or a zero. A zero is a real
value in this domain and must remain distinguishable from an absent one.

This is the single most important rule in the frontend. Every other UI concern here is
cosmetic; this one is correctness.

## Theme

Dark. One palette defined as CSS custom properties on `:root`, no framework, system font
stack. `lightweight-charts` is configured with the same palette so the chart does not sit
in the page as a light rectangle.

Layout is CSS grid for the three-pane shell and flex within panes. Wide content (the
catalog table) scrolls inside its own pane rather than the page scrolling horizontally.

## Error handling

The backend already maps service exceptions to clean JSON via
`wfm/gui/errors.py::install_error_handlers`. The frontend surfaces those in a dismissible
banner rather than a browser alert, and never leaves a control in a pending state after a
failure.

Two cases get specific treatment:

- **Daemon stop.** Confirms first, warning that stopping the daemon disconnects the
  dashboard, because the daemon is the process serving it. This consequence is accepted
  and documented in the phase 8 GUI design. After confirmation the page shows a
  "disconnected, daemon stopped" state rather than an error, since the connection dropping
  is the expected outcome.
- **WebSocket drop.** Reconnects with backoff. While disconnected the Signals tab shows a
  stale indicator rather than silently displaying an unchanging list, so a dead feed is
  never mistaken for a quiet market.

## Testing

**Backend.** Per the phase 8 design, GUI route tests stay thin: routing, error mapping and
serialization only. Logic is tested where it lives.

- `catalog_service.browse` gets a real service test. This is where the logic is: paging
  arithmetic, the total-versus-page distinction, and `LIKE` escaping.
- Route tests for `GET /catalog` covering the edges: offset past the end returns an empty
  page with a correct total, an empty query returns everything, a query containing `%` or
  `_` is escaped rather than treated as a wildcard, and `limit` is clamped.
- A route test for `GET /signals` confirming the filters reach `list_signals`.
- `report_service.history` gets a service test: the anchoring (a 90 day request against a
  database whose newest candle is yesterday returns 90 days, not 89), the `days` clamp, and
  rank resolution defaulting to `canonical_rank`. A route test covers serialization and an
  unknown slug mapping to a clean error rather than an empty list, so a typo is
  distinguishable from an item with genuinely no history.
- `tests/gui/test_real_server_smoke.py` extends to `/` and `/catalog`, keeping its role as
  the only test exercising production connection settings against a real server.

TDD throughout, each test confirmed red for its stated reason before the code exists, as
in every prior phase here.

**Frontend.** No test framework. The phase 8 design already accepted type-checking-only
for the frontend of a personal tool, and the vanilla build has no toolchain to run even
that. Verified manually against a copy of the real database, never the live one.

## Out of scope

- Deep links and browser history. Tab and selection state is in-memory; a refresh returns
  to the Catalog tab.
- Item detail for group members beyond the arbitrage roll-up the group endpoint returns.
- Any write action beyond watchlist add/remove, group CRUD and membership, and daemon
  stop. In particular the GUI records no trades: the ledger is read-only here, as in the
  phase 8 design.
- Starting the daemon from the dashboard, which is impossible by construction. The daemon
  is the process serving the page.
- The read-only non-goal is unchanged: the GUI never places, edits or cancels a
  warframe.market order.
