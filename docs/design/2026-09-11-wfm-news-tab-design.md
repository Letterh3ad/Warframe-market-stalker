# Frontend module split + the read-only News tab (phase 9a, plan 4)

**Status:** approved 2026-09-11, not yet built.
**Parent spec:** `docs/design/2026-09-05-wfm-news-bias-design.md` (## GUI). This document
amends that section; where they disagree, this one wins and says why.
**Phase:** 9a, the last build-order step. Nothing here reads a bias or touches a Signal.

## What this builds

1. `index.html`'s single 1697-line inline script becomes native ES modules: a `lib/` of
   shared helpers, one module per tab, and `app.js` as wiring.
2. A News tab. "Read-only" means nothing in the UI edits, relabels or deletes a row:
   what you see is what the pipeline produced. Its two buttons start the same ingest and
   classify jobs the daemon already runs hourly, and classify does write events -- it is
   the pipeline running on demand, not the UI editing the corpus.
3. `wfm/gui/routes/news.py` and a single-flight job runner shared with the daemon tick.

## Why now

`index.html` is 1697 lines and this feature adds a tab, which trips the exit condition
recorded in `DECISIONS.md` on 2026-09-05. The parent spec's answer stands: native ES
modules, no build step, not Svelte.

## Amendment 1: split all eight modules at once, not one tab at a time

The parent spec says "tabs migrate one at a time rather than in a rewrite with no test
net". The migration is still incremental per commit; what changes is that plan 4 finishes
it rather than stopping after News.

The reason is the coupling, which is smaller than 1697 lines suggests. Per-tab state is
already tab-local (`state.catalog`, `signalsFeed`, `groupsState`, `lastDaemonStatus`,
`catalogReqSeq`, `groupSeq`). There are exactly two cross-cutting seams:

- `selectItem(slug, rank, meta)`, called from catalog, watchlist, signals, groups and the
  detail pane's own rank picker: 7 call sites.
- `selectTab()` reaching into detail-pane internals, calling `disposePriceChart()` and
  bumping `detailSeq` to invalidate in-flight `/items/{slug}` and `/groups/{name}/analysis`
  responses.

Both seams have to be designed the moment the first module leaves the file. A partial
split designs them and then leaves them half-applied, with some callers importing and
others reading globals out of the same file. That half-state is worse than either end and
would be the resting state for however long the remaining tabs took.

The cost is verification, which does not shrink by deferring: there is no JS test net, so
it is manual clicking either way. Risk concentrates in three known-fiddly places, and each
gets its own commit and its own click-through: the Signals WebSocket with its reconnect and
bfcache handling, the lightweight-charts lifecycle in the detail pane, and the monotonic
seq guards that stop a late response painting the wrong tab.

**Mitigations, all three required:**

- Moves are verbatim. No rewriting while moving; cleanup is a separate commit afterwards,
  so any regression bisects to a move or to a change, never to both at once.
- One module per commit, each ending with a click-through of the affected tab against the
  live daemon on `http://127.0.0.1:8420`.
- An import-graph test (below). A typo'd `import "./lib/fmt.js"` is a silently blank tab
  with a console error no existing test can see.

## Amendment 2: the feed is events, not articles

The parent spec sketches `GET /news?limit=&offset=&source=` as paged articles with a
drill-down. The tab is instead built on events, because the question it exists to answer
today is "is the classifier right?", not "what happened this patch".

That question is live: the first full local run labelled 141 events and reported
`confidence = high` on 133 of them, including rows it had labelled `other` / `unclear`.
The model is not calibrated, and no accuracy measurement exists. An event-first view is
also the surface a gold-set labelling pass would use.

Articles remain reachable at `GET /news/articles/{id}` for context.

## Module layout

```
wfm/gui/static/
  index.html        shell: markup, styles, <script type="module" src="/static/app.js">
  app.js            builds TABS from the tab modules, selectTab, boot
  lib/api.js        api()
  lib/dom.js        el, showError, clearError, humanAge
  lib/fmt.js        fmt, signClass, numTd, statLine, statTable, missingLine
  lib/detail.js     selectItem, rankPicker, renderDetail, chart lifecycle
  tabs/catalog.js   tabs/watchlist.js   tabs/signals.js
  tabs/groups.js    tabs/daemon.js      tabs/ledger.js
  tabs/news.js
```

Each tab module exports `{ id, label, render }`, so `app.js` builds the registry from its
imports instead of maintaining a literal that can drift from the functions it names.

`lib/detail.js` exports `selectItem`, `dispose()` and `invalidate()`. `selectTab` calls the
latter two rather than reaching for `disposePriceChart()` and `detailSeq++`, which is what
makes the detail pane a module with an interface rather than a pile of shared globals.

## Endpoints (`wfm/gui/routes/news.py`)

```
GET  /news/events?limit=&offset=&event_type=&source=   events + their links, newest first
GET  /news/articles/{id}                               one article, its events, their links
GET  /news/status                                      backend, model, counts, job state
POST /news/refresh                                     start an ingest job
POST /news/classify                                    start a classify job
```

Ordering is `COALESCE(effective_at, published_at) DESC`, matching how the repo already
orders linked events. Paging mirrors `/catalog`: `limit`, `offset`, and a `total`.

`source` lives on the article, not the event, so filtering by it joins `news_articles`;
`event_type` filters `news_events` directly. Both are optional and combine. `/news/status`
reports `job: null` until a job has run in this process -- the job slot is in-memory, so a
daemon restart clears it, and that is the honest answer rather than a fabricated history.

No `/news/item/{slug}`, no `news_bias=true` parameter, no Signals toggle. Those need
`NewsIndex` and the event study, and they are 9b.

## The job runner (`wfm/services/news_jobs.py`)

Ingest takes ~30s (one request per source per second) and classify takes minutes. Neither
belongs inside a request, and two classify runs over the same pending batch would write
every event twice.

One module-level job slot plus an `asyncio.Lock`:

- `run(ctx, kind)` awaits the work. The daemon tick calls this, which is how it joins the
  lock; `runner._run_news` stops calling `news_service.ingest` / `.classify` directly.
- `spawn(ctx, kind)` starts a task and returns immediately. The POST routes call this.
- A start while a job is in flight is refused: `{"started": false, "reason": "..."}`.
  Refused, not queued -- the tick runs hourly anyway, so a dropped manual run costs
  nothing, while a queue would need a queue's error handling.
- Progress rides the `on_progress` callback the tick already passes to `classify`: the job
  records `done` against a `total` read before the run starts.

The GUI and the daemon share one process, one event loop and one `AppContext` by design
(`daemon_service.start` builds the app and serves it in a task), so the lock genuinely
covers both.

**Known hole, accepted:** the lock is per-process. A `wfm news classify` typed in another
terminal while a GUI job runs can still double-classify a batch, because both read
`pending()` before either marks anything. Closing it properly means claiming rows in the
database, which means a migration, for a race that takes deliberate effort to hit. Recorded
in `DECISIONS.md` rather than fixed.

## The News tab

A status strip: backend, model, article and pending counts, and the two buttons, each
showing its in-flight state. While a job is live the tab polls `/news/status` every 2s and
stops when it finishes, the same shape the Daemon tab already uses for `/daemon/status`.

Then the event feed, newest first, with filter chips for event type and a source badge per
row. Each row carries: event type, subject, direction, strength, confidence, the excerpt
the classifier saw, and the linked slugs with their `link_method` badge and weight.
Clicking a slug calls the imported `selectItem`, so the existing detail pane opens from
this tab like any other.

## Testing

| File | Covers |
|---|---|
| `tests/gui/test_news_routes.py` | the three GETs, both POSTs, the single-flight refusal, paging and filters |
| `tests/services/test_news_jobs.py` | the lock: a second start refused, tick and spawned job cannot overlap, progress in the status, a failing job records the error AND releases the lock |
| `tests/gui/test_static_modules.py` | every `import` in every module resolves to a path the server actually serves |

The static-module test is the only new kind. It reads each `.js` under `static/`, extracts
the specifiers from its `import` statements, and asserts each resolves to a file inside
`static/`. It is deliberately not a JS parser: a regex over `from "..."` catches the whole
failure class (a path typo, a moved file, a missing extension) without pretending to
understand the language.

Manual verification stays manual and is part of each commit's definition of done.

## Out of scope

- Anything reading a bias: `NewsBias`, `features/news.py`, the Signals toggle, evidence
  chips, the news-only panel. All 9b, all gated on the event study.
- The date-copying defect and the gold set. Both live in the 9b list already.
- Any migration or index.
