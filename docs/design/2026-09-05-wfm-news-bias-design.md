# WFM News Bias: design

**Date:** 2026-09-05
**Status:** approved in brainstorm, not yet planned or built
**Phase:** 9a (independent of the Pi/distributed phase 9)

## Problem

Warframe item prices are driven by supply and demand shocks that are *announced*, not
discovered. Prime Vault rotations cut supply. Unvaultings and Prime Access flood it.
Ability reworks and mod nerfs shift demand. These events are scheduled, publicly posted,
and directionally predictive of plat prices.

The tool currently analyses market data only. It has no idea a vaulting was announced
yesterday.

## Goal

Ingest text news from a small set of sources, extract structured events, link those
events to catalog slugs, and let the user apply a **news bias** to analyzer output from
the GUI on demand.

## Non-goals

- Automatic alerting on news. The daemon collects and classifies; it never acts.
- Replacing or modifying any existing analyzer.
- Video sources (devstreams, YouTube).
- Twitter/X. The API is roughly $100/month and @PlayWarframe duplicates warframe.com.
  Dropped entirely, with no placeholder.

## Honest risk assessment

Recorded because it shaped the design, and because it is the thing to re-check after the
first backtest.

1. **Item linkage is harder than classification.** "Condition Overload damage reduced"
   maps to one item. "Mesa Prime enters the Vault" maps to the set, its five parts, her
   signature weapons and the relics. Fuzzy matching gets the frame name and nothing else.
2. **Direction is not obvious even when the event is.** A vaulting raises built parts and
   relics; a Prime Access release lowers the same parts. A nerf can raise a price by
   making an alternative scarce. A confidently wrong bias is worse than no bias.
3. **Without historical replay you cannot know if it helps.** This is what usually turns
   a feature like this into a toy.
4. **The market reads the same news.** Official announcements reprice within hours. The
   thin edge on official news is real but small; the fatter edge is second-order (which
   part of a vaulted set is the bottleneck) and in community-reaction lag.

The design answers these with: local set expansion instead of model trivia (1), per-item
direction on the link row (2), an `as_of` replay path built into the same code production
uses (3), and honest measurement per source and per event type (4).

## Decisions taken in the brainstorm

| Question | Decision |
|---|---|
| How news affects analysis | Bias multiplier as a post-pass. Analyzers untouched. |
| Which sources | warframe.com/news, official forum update/hotfix notes, r/Warframe. |
| Twitter/X | Dropped entirely. |
| Classifier | Pluggable. Local Ollama (small Qwen, non-thinking) default; Claude alternate. |
| Fetch/classify timing | Background, in the daemon. |
| Bias application timing | GUI, on demand, opt-in toggle. |
| Bias power | Confidence primarily. Direction can never flip. Plus news-only surfacing. |
| Frontend | Stay vanilla, split to native ES modules. See `DECISIONS.md`. |

## Architecture

```
sources ──▶ fuzzy gate ──▶ classifier ──▶ linker ──▶ SQLite
 (http)      (local,        (Ollama |     (local,     (3 tables)
             free, drops     Claude |     catalog          │
             non-matches)    Fake)        expansion)       │
                                                           ▼
                                                      NewsIndex
                                              (one query, in memory, O(1))
                                                           │
                        ┌──────────────────────────────────┼───────────────┐
                        ▼                                  ▼               ▼
                  bias post-pass                    news-only list    backtest
                (analysis_service)                                  (as_of replay)
```

### Module layout

```
wfm/news/
  types.py          Article, ExtractedEvent, ItemLink, EventType
  schema.py         ONE JSON schema, shared by both backends
  sources/          base.py (Source protocol), warframe_news.py, forums.py, reddit.py
  match.py          fuzzy gate                        pure
  link.py           subject -> slugs                  pure, catalog passed as data
  classify/         base.py (Classifier protocol), ollama.py, claude.py, fake.py

wfm/features/news.py          links + now -> NewsBias  pure, no DB
wfm/store/news.py             NewsRepo
wfm/store/migrations/m0004_news.py
wfm/services/news_service.py  ingest + query orchestration
wfm/gui/routes/news.py        endpoints
```

### Layering

`tests/test_architecture.py` is extended: `wfm/news/**` must import neither `wfm.gui` nor
`wfm.services`. `wfm/features/news.py` is already covered by the existing purity test.

The bias is applied as a **post-pass in `analysis_service`**, so `wfm/analyzers/` is never
touched and its architecture test stays green.

## Data model (migration `m0004`)

The model reads each article exactly once and extracts facts. Everything downstream reads
the facts. No component ever re-reads an article or re-invokes the model.

### `news_articles`

```
id, source, external_id UNIQUE, url, title,
published_at, fetched_at, content_hash,
excerpt,                        -- only the matched sentences, for audit
status,                         -- pending | classified | no_match | failed
classifier_name, classifier_version, classified_at
```

`external_id` deduplicates on fetch. `content_hash` catches an edited hotfix note and
re-queues it. `excerpt` lets the GUI show the actual sentence behind any bias. Full
bodies are not stored by default.

### `news_events`

One row per event. A single hotfix note contains many.

```
id, article_id FK,
event_type,     -- vault_in | vault_out | prime_access | buff | nerf
                -- | drop_rate_change | new_content | rework | other
subject_raw,    -- the entity as the model named it
direction,      -- up | down | unclear
strength,       -- 0..1
confidence,     -- 0..1
rationale,      -- one sentence
effective_at,   -- when it bites, not when it was posted
raw_json        -- nullable, model's literal output, written only when configured
```

`effective_at` separate from `published_at` is load-bearing: the gap between announcement
and effect is the window in which the user can still act.

### `news_item_links`

```
event_id FK, slug, rank,
link_method,   -- exact | fuzzy | set_expansion | curated
link_score,    -- fuzzy score
direction,     -- PER ITEM, may differ from the event's
weight         -- 1.0 direct, ~0.8 set sibling, fuzzy scaled by score
```

**The separation between `news_events` and `news_item_links` is the key structural
choice.** The model produces events (a name, as text). Code produces links (resolved
slugs) via fuzzy match plus set expansion off the existing catalog. Consequences:

- Linkage can be re-run when the catalog grows or the fuzzy threshold changes, without
  re-invoking the model (`replace_links_for`).
- The classifier can be swapped without touching linkage.
- `link_method` makes every attachment explainable in the GUI.

Per-item direction is not pedantry: one vaulting event drives built parts and relics in
opposite directions from a Prime Access release touching the same slugs.

### How per-item direction is derived

`link.py` decides it from `event_type` crossed with the item's **role**, and role comes
from `Item.tags`, which the catalog already carries (`set`, `relic`, `mod`, `warframe`,
`primary`, and so on). No model involvement.

| event_type | set / parts | relics | related mods |
|---|---|---|---|
| `vault_in` (supply cut) | up | up | none |
| `vault_out` / unvaulting (supply flood) | down | down | none |
| `prime_access` (release) | down | down | none |
| `buff` | up | up | up |
| `nerf` | down | down | down |
| `rework` | unclear | unclear | unclear |
| `drop_rate_change` | inverse of rate change | inverse | none |
| `new_content` | up | none | up |
| `other` | unclear | unclear | unclear |

`unclear` yields `sign = 0`, so a rework contributes nothing until a human or a better
classifier says otherwise. That is the intended conservative default: the table encodes
only relationships confident enough to trade on, and everything else abstains.

The table lives in `link.py` as data, not as branching logic, so correcting a row after
the backtest is a one-line change.

### Indexes

```
news_articles(external_id) UNIQUE     dedupe
news_articles(status)                 the pending-work queue
news_articles(published_at DESC)      the News tab feed
news_events(article_id)               join
news_events(effective_at)             the horizon filter, in every bias query
news_item_links(slug, rank)           the hot path
news_item_links(event_id)             "what did this event touch?"
```

### `NewsRepo` surface

```
# write (one transaction per article: article + its events + its links)
upsert_article(...) -> id | None       None when external_id+content_hash unchanged
mark(article_id, status, classifier, version)
insert_events(article_id, events) -> ids
insert_links(event_id, links)
replace_links_for(event_id, links)

# read
pending(limit)
active_links(now, horizon, as_of=None)   ONE query, feeds NewsIndex
events_for_item(slug, rank, limit)
links_for_event(event_id)
recent_articles(limit, offset, source=None)
```

## NewsIndex

Only news inside the decay horizon can affect anything. A busy month is roughly 200
articles, 600 events, 2000 links: well under a megabyte. So the active window is loaded
whole rather than queried per item.

```python
NewsIndex.build(repo, now, horizon_days, as_of=None)
  -> dict[(slug, rank)] -> tuple[LinkedEvent, ...]
  -> .bias_for(slug, rank) -> NewsBias | None      O(1)
```

Cached on `app.state` with a TTL and invalidated on ingest, exactly as
`wfm/gui/market_cache.py` does for `MarketContext`. Decorating a 500-row catalog page
costs one query, not 500.

`NewsIndex` is a plain value object, so the pure function in `features/news.py` consumes
it directly and stays database-free.

**`as_of` is what buys the backtest.** It filters `published_at <= as_of`, yielding
exactly what the system knew on that date, through the same builder production uses. The
backtest therefore exercises real logic instead of a parallel reimplementation.

## Classifier

```python
class Classifier(Protocol):
    name: str
    version: str
    async def classify(self, req: ClassifyRequest) -> list[ExtractedEvent]: ...
```

Implementations: `OllamaClassifier`, `ClaudeClassifier`, `FakeClassifier`.

`classifier_name` / `classifier_version` are stored per article, so switching backends
does not corrupt history and allows selective invalidation.

### One schema, both backends

The JSON schema is defined once in `wfm/news/schema.py`. Ollama takes it as `format`,
Claude as `output_config.format`. Both constrain decoding, so a parse failure is
structurally impossible rather than something to defend against.

### The model's output contract is coarse on purpose

The model does **not** emit the numbers that reach the database. It emits labels, and
code maps labels to numbers. This is the single biggest accuracy lever available, and it
matters more than the choice between a 4B and an 8B model.

```jsonc
{
  "event_type": "vault_in | vault_out | prime_access | buff | nerf
                 | rework | drop_rate_change | new_content | other",
  "direction":  "up | down | unclear",
  "strength":   "minor | moderate | major",
  "confidence": "low | medium | high",
  "timing":     "immediate | dated | unknown",
  "date_text":  "September 20"        // or null
}
```

**Why enums rather than floats.** Small models cannot produce calibrated probabilities.
Asked for a 0-to-1 confidence, a 4B returns 0.8 for nearly everything, which is noise
wearing the costume of a measurement. Asked to choose among three labels, the same model
is reliable. Code maps them:

```
strength:   minor 0.3   moderate 0.6   major 0.9
confidence: low   0.4   medium   0.7   high  0.95
```

The mapping lives in config, so the backtest can retune it **without re-classifying a
single article**. `news_events.strength` and `.confidence` stay `REAL`, so the schema is
unaffected.

**Why the model never computes a date.** Date arithmetic over relative expressions
("next Tuesday", "with the next mainline") is where small models fail hardest, and a
wrong `effective_at` does real damage because it anchors the decay curve. So the model
reports `timing` plus the date **as written**, and code parses it against the article's
publish date. If parsing fails, `effective_at` stays NULL and the anchor falls back to
`published_at`, which the decay function already handles. A failed date degrades safely
instead of poisoning a bias.

`raw_json` stores this label payload verbatim when enabled, so a mapping change can be
audited against what the model actually said.

### Ollama backend

Ollama 0.32.3 is already installed locally. It serves HTTP on `localhost:11434` and the
project already depends on `httpx`, so **the local backend adds no Python dependency**.

Target hardware is an RTX 5060 Laptop with 8GB VRAM. The existing local model library
(30B and 32B models at 18-19GB) does not fit and would spill to CPU.

**Starting recommendation: Qwen3-4B-Instruct-2507 at Q8** (roughly 4.3GB resident,
leaving ample room for context). Fallback if date and event-type accuracy disappoint:
Qwen3-8B at Q4_K_M or Q5_K_M.

Two reasons for that specific choice:

- **The `-Instruct-2507` line is non-thinking by construction**, not a hybrid suppressed
  with `/no_think`. A model that cannot think is more reliable here than one instructed
  not to. Anything tagged `-Thinking-` is disqualified.
- **Prefer 4B at Q8 over 8B at Q4.** Both fit. But with constrained decoding the model
  never generates prose, it only ranks a few enum tokens, so accuracy here is about
  *logit precision*, which is exactly what Q4 degrades. Parameter count buys world
  knowledge and reasoning depth, and this design deliberately needs neither: slug
  expansion moved to `link.py` and calibration moved to the label mapping above. The 4B
  is also roughly twice as fast, which is the difference between an overnight and a
  weekend backfill.

Exact Ollama tag to be confirmed with a `pull` at implementation time; registry naming
shifts.

Graceful degradation: if Ollama is not running, ingest records `status='failed'` and the
GUI reports the classifier as unavailable. Nothing else breaks.

### Claude backend

Official `anthropic` SDK (not raw HTTP, not an OpenAI-compatible shim). Haiku 4.5 by
default at $1/$5 per million tokens. The shared system prompt is cached. At realistic
volume this is roughly $3 to $5 per month.

### Per-candidate prompting

**Do not hand a small model a full hotfix note and ask for a list of events.** That is a
hard extraction task and 4B models are unreliable at it.

The fuzzy gate already knows which item names appear and where. So the model receives
**one candidate plus its surrounding sentences at a time**:

```
Published: 2026-09-06
Context: "Mesa Prime, Akjagara Prime and Redeemer Prime will enter the Prime Vault on
          September 20th, after which their relics will no longer drop."
Subject: Mesa Prime
Classify: event_type, direction, strength, confidence, timing, date_text.
```

This converts hard extraction into constrained classification, which small models do
well. A note with 15 candidates becomes 15 calls of roughly 300 tokens each: seconds on a
local 4B, fractions of a cent on Haiku.

The publish date is included so `date_text` can be resolved by code against a known
anchor rather than by the model against nothing.

### Choosing the model empirically

The recommendation above is a starting point, not a conclusion. Settle it with a
measurement, using infrastructure this design already builds:

1. Take 50 real articles from the captured fixture corpus.
2. Run them through the **Claude backend once** and hand-correct the output. That is the
   gold set, and it costs cents.
3. Score each candidate local model against it on per-field agreement (`event_type`
   exact, `direction` exact, `strength`/`confidence` within one label, `effective_at`
   within a day).

Using the Claude backend as the labeller for the local backend is the reason the
two-backend seam earns its keep beyond redundancy. Compare at minimum Qwen3-4B-Instruct
at Q8, Qwen3-8B at Q4, and the already-present `gemma4:12b` as a ceiling check.

## Sources

`Source` protocol returning raw `Article` values. Implementations:

- `warframe_news.py` -- warframe.com/news. Carries Prime Access and Vault announcements.
- `forums.py` -- official forum update/hotfix notes.
- `reddit.py` -- public JSON listings, no key required. **Disabled by default in config.**

Reddit ships complete but off, so a week of output can be inspected and the source either
enabled or deleted. Its listings only page back roughly 1000 posts, so its history is thin
and it contributes little to the backtest.

### Rate limiting

News fetching gets its **own** budget and must never touch the market `TokenBucket`.
That bucket exists to keep the tool compliant with warframe.market's 3 req/s;
warframe.com and reddit.com are unrelated hosts. This is deliberately the opposite of the
GUI-shares-the-daemon-budget rule in `CLAUDE.md`, and for the same underlying reason:
one budget per upstream.

`HttpCacheRepo` is reused for conditional GETs, so a poll finding nothing new costs a 304.

## Bias math

### Per-event score

```
s_i = sign(direction_i) · strength_i · confidence_i · weight_i · decay_i
```

`sign`: up = +1, down = -1, **unclear = 0** (drops out rather than guessing).

```
anchor = effective_at or published_at

now <  anchor  ->  decay = 1.0                                    the trade window
now >= anchor  ->  decay = 0.5 ** (days_since / half_life[event_type])
```

Full strength between announcement and effect, because that gap is exactly when the user
can still act. Decay afterwards as the market finishes pricing it in.

### Dedupe, then saturate

Triple coverage is guaranteed: warframe.com, the forums and Reddit all report the same
vaulting. Summing would let repetition masquerade as evidence.

Dedupe on `(event_type, normalised subject, effective_at to the day)`, keeping the highest
`confidence · weight` per group. Then:

```
score = tanh( Σ s_i / news_saturation )     -> (-1, 1)
```

### Application

```
alignment = +score  if signal is BUY
            -score  if signal is SELL
             0      if signal is HOLD

confidence' = clamp( confidence · (1 + k_conf · alignment), 0, 1 )
magnitude'  = magnitude · (1 + k_mag · max(alignment, 0))     capped at news_magnitude_cap
```

Two guaranteed properties:

- **Direction cannot flip, structurally.** The factor is `1 + k·alignment` with `k < 1`
  and `alignment >= -1`, hence always strictly positive. Sign preservation is arithmetic,
  not a guard that can be forgotten. `Config.__post_init__` rejects `k_conf >= 1`, in the
  same style as the existing `requests_per_second` validation.
- **Magnitude only grows, never shrinks.** Magnitude is a measured market quantity; news
  has no business shrinking a measurement. Confidence is epistemic, which is what news
  informs. Starting at `k_mag = 0` (confidence-only) is recommended until the backtest
  justifies otherwise.

### Worked example

Mesa Prime unvaulted. Article 3 days old, effective today. Event `vault_out`, direction
down, strength 0.8, model confidence 0.9. Direct link to `mesa_prime_set`, weight 1.0.

```
decay = 1.0                              (now == anchor)
s     = -1 · 0.8 · 0.9 · 1.0 · 1.0 = -0.72
score = tanh(-0.72)                = -0.62
```

`flip` independently says BUY, magnitude 0.60, confidence 0.70.

```
alignment   = -0.62
confidence' = 0.70 · (1 - 0.31) = 0.48
magnitude'  = 0.60               (unchanged, alignment negative)
```

Still a BUY. It just stops looking like a good one, and the GUI can say why.

### Null honesty

An item with no news yields `NewsBias = None`, **not zero**. The GUI renders "no news",
never `0.00`. Zero would assert that news is neutral on this item; `None` states the
truth, which is that nothing was found. Consistent with `Provenance` and the `fmt` guard.

### Audit payload

`Signal.evidence` is already a dict, so this costs nothing:

```python
evidence["news"] = {
  "score": -0.62,
  "confidence_before": 0.70, "confidence_after": 0.48,
  "events": [{
     "article_id": 41, "title": "Prime Vault: Mesa Prime Returns", "url": "...",
     "event_type": "vault_out", "direction": "down", "link_method": "direct",
     "contribution": -0.72, "age_days": 3, "excerpt": "Mesa Prime, Akjagara Prime..."
  }]
}
```

Every biased number traces back to the sentence that caused it. This was a condition of
the feature being worth building.

### News-only surfacing

Items where `|score| >= news_only_threshold` and no open signal exists, ranked by
`|score|`.

These emit a **`NewsWatch`, not a `Signal`**. Nothing an analyzer did not produce enters
the signals table, so "signal" keeps meaning "a market analyzer fired".

### Config

```
news_enabled          = false     master switch, off until the backtest says otherwise
news_sources          = ["warframe_news", "forums"]      reddit off by default
news_classifier       = "ollama"  ollama | claude | none
news_model            = "<qwen3 4b instruct tag, confirm with a pull>"
news_ollama_url       = "http://localhost:11434"
news_claude_model     = "claude-haiku-4-5"

# label -> number mapping, retunable from the backtest with no reclassification
news_strength_map     = { minor = 0.3, moderate = 0.6, major = 0.9 }
news_confidence_map   = { low = 0.4, medium = 0.7, high = 0.95 }
news_horizon_days     = 60
news_saturation       = 1.0
news_k_confidence     = 0.5
news_k_magnitude      = 0.0       start confidence-only
news_magnitude_cap    = 1.5
news_only_threshold   = 0.35
news_store_raw_json   = false
news_half_life        = { vault_in = 30, vault_out = 21, prime_access = 21,
                          rework = 21, nerf = 14, buff = 14,
                          drop_rate_change = 30, new_content = 10, other = 7 }
```

All tunable without a migration, which is the payoff for keeping decay out of the schema.

## GUI

### Endpoints (`wfm/gui/routes/news.py`)

```
GET  /news?limit=&offset=&source=      recent articles, paged like /catalog
GET  /news/{id}                        article -> events -> linked items
GET  /news/item/{slug}?rank=           NewsBias + contributing events
GET  /news/watch                       the news-only list
GET  /news/status                      backend, model, pending count, last ingest
POST /news/refresh                     trigger ingest now
```

Existing endpoints take **additive query params only**, so no contract changes:

```
GET /signals?...&news_bias=true
GET /items/{slug}?...&news_bias=true
```

### Surfaces

- **News tab.** Article feed with source badges. Drill into extracted events, each showing
  linked items with a `link_method` badge. Clicking an item calls the existing
  `selectItem()`. The "news but no signal" panel lives here rather than as its own tab.
- **Item detail.** A news section: `None` renders "no recent news", otherwise the score and
  each contributing event with excerpt, age and link.
- **Signals tab.** An "apply news bias" toggle, **default off**. When on, confidence renders
  as `0.70 -> 0.48` with an expandable evidence chip.
- **Catalog / Watchlist.** A small directional chip on rows with active news. One
  `NewsIndex` lookup per row, zero extra queries.

### Frontend structure

`index.html` is 1655 lines and this feature adds a tab, which trips the exit condition
recorded in `DECISIONS.md` on 2026-09-05.

**Decision: stay vanilla, but split into native ES modules.** No build step required:

```html
<script type="module" src="/static/app.js"></script>
```

`app.js` imports `./tabs/news.js`, `./tabs/signals.js`, `./lib/fmt.js`. Tabs migrate one at
a time rather than in a rewrite with no test net. News lands as a new module instead of 300
more lines in the pile. If a second substantive feature arrives and modules are not
holding, that is the real Svelte moment. Needs its own `DECISIONS.md` entry, since it
amends a recorded one.

## Testing

| Seam | Buys |
|---|---|
| `FakeClassifier` | Canned input to events. Suite stays offline, mirroring `tests/fakes/api.py`. |
| `FakeSource` | Fixture articles from disk, no network. |
| Fixture corpus | Extend `scripts/capture_fixtures.py` to snapshot real source payloads. |
| `FakeClock` | Already exists. Decay is clock-driven, so decay tests are exact and instant. |
| Pure modules | `match.py`, `link.py`, `features/news.py` take plain values. No DB, no mocks. |
| `live` marker | `addopts = "-m 'not live'"` already exists. A `@pytest.mark.live` contract test hits real Ollama and real Claude and asserts both still honour the shared schema. |

```
tests/news/{test_match,test_link,test_sources,test_classify_ollama,test_classify_claude}.py
tests/features/test_news.py            decay, dedupe, saturation      pure, exact
tests/store/test_news.py               repo + m0004
tests/services/test_news_service.py    ingest orchestration, transactional integrity
tests/gui/test_news_routes.py
tests/test_architecture.py             extended for wfm/news
```

The property test to write explicitly: **for all inputs, `sign(biased) == sign(unbiased)`**.
Arithmetically guaranteed, cheap to assert, and it locks the guarantee against a future
edit to `k_conf`.

## Backtest

`wfm/validation/harness.py` already replays day by day with `as_of` slicing and forward
return scoring, returning `ReplayResult` with hit rate, median forward return and a
per-direction breakdown. The news backtest extends it rather than duplicating it.

```python
replay(ctx, analyzer, start, end, horizon_days, news_index=None)
```

When supplied, the bias post-pass runs inside the day loop using
`NewsIndex.build(repo, now=day, as_of=day)`, so the replay only sees news published by
that day. No leakage, and it is the same builder production uses.

```
wfm news backtest --from 2026-01-01 --to 2026-09-01
```

Runs the identical range twice, biased and unbiased. Same days, same signals, only
confidence differs. Reports hit rate at each confidence cutoff, **split by `event_type`
and by `source`**. That answers not just "does it help" but which parts do. If vault
events run 70% and buff/nerf are coin flips, the coin flips get zero weight. If Reddit
contributes nothing over three months, the source is deleted and the seam kept.

`sweep_thresholds()` already exists, so sweeping `k_conf` and the half-lives is nearly
free.

**Historical backfill is why the local model matters more than cost.** warframe.com's news
archive and the forum update-notes subforum page back years, roughly 1500 reachable
articles, which is on the order of 8000 tiny per-candidate calls. On Haiku that is a real
if modest bill one would hesitate to re-run. On a local Qwen it is unattended GPU time and
free, so the whole backfill can be re-run every time the prompt changes. That is what turns
the backtest from an intention into a habit.

Caveat already noted: Reddit's listings reach back only about 1000 posts, so its history is
thin.

## Suggested build order

Each step ships green and is independently useful.

1. `m0004` + `NewsRepo` + types. Storage first, nothing reads it yet.
2. Sources + fixtures + `match.py` fuzzy gate. Ingest raw articles, classify nothing.
   Verifiable by eye: is the gate finding real items and rejecting noise?
3. `Classifier` protocol + `FakeClassifier` + `OllamaClassifier` + `schema.py` + the
   label-to-number mapping. Then the 50-article benchmark to pick the model, with the
   `ClaudeClassifier` brought forward if it is needed to produce the gold labels.
4. `link.py` set expansion + `news_item_links`. Now events resolve to slugs.
5. `features/news.py` (decay, dedupe, saturation) + `NewsIndex`. Pure, heavily tested.
6. `news_service` ingest orchestration + daemon tick.
7. Bias post-pass in `analysis_service` + the sign-preservation property test.
8. Backfill + backtest. **Gate: does it actually help?** Tune or cut here.
9. Frontend ES module split, then the News tab and the bias toggle.
10. `ClaudeClassifier` as the alternate backend.

Step 8 before step 9 is deliberate. Do not build the UI for a bias that has not been shown
to work.

## Open questions for implementation

- Exact Ollama model tag. Starting recommendation is Qwen3-4B-Instruct-2507 at Q8;
  confirm the tag with a `pull` and settle the choice with the 50-article benchmark in
  "Choosing the model empirically" rather than by argument.
- Whether the three-label `strength` and `confidence` scales are granular enough, or
  whether a five-label scale helps once there is backtest data. Start at three.
- Whether the curated frame-to-signature-weapon table is needed for v1, or whether direct
  match plus set expansion is good enough. Decide after step 4 on real data.
- Forum HTML parsing may need a fallback if the markup shifts. Consider whether the forum
  source is worth its maintenance cost versus warframe.com alone.
- Whether `news_enabled` should stay off by default permanently, or flip on after the
  backtest passes.
