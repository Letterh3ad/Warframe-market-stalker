# WFM News Bias: design

**Date:** 2026-09-05, rescoped 2026-09-06 after adversarial review
**Status:** approved; **v1 scope reduced, see "What v1 actually ships"**
**Phase:** 9a (corpus + classifier), 9b (bias + validation). Independent of the
Pi/distributed phase 9.

## What v1 actually ships

An adversarial review on 2026-09-06 found a blocking fact this design had assumed rather
than checked. **`daily_stats` holds 93 distinct dates and no series has the 97 days
`replay()` requires** (`PRICE_WINDOW_DAYS = 90` plus a 7-day forward horizon).
warframe.market serves a 90-day rolling window, so history only grows forward at one day
per day. The step-8 backtest gate, which steps 9 and 10 were gated on, cannot run today
and will not for roughly six months.

Both datasets this feature needs are time-limited: price history grows a day at a time,
and the news corpus is empty. So v1 collects both in parallel and defers all judgement.

| | Phase 9a (v1, build now) | Phase 9b (later, gated) |
|---|---|---|
| Sources, fuzzy gate, `news_candidates` | yes | |
| Classifier, events, links | yes | |
| Read-only News tab | yes | |
| `NewsIndex`, bias post-pass | | yes |
| News-only surfacing, bias toggle | | yes |
| Validation | | **event study**, not analyzer replay |

**Nothing in 9a touches a Signal**, so nothing can degrade `discord_min_confidence` or
any existing behaviour. Everything below describes the full design; the table above says
which half is being built first. The bias math, `NewsIndex` and GUI-toggle sections are
retained as the 9b specification, not deleted.

### Why the classifier is in v1 rather than deferred

Labelled events also have to accumulate, and months of eyeballing real classifications on
real articles is the only honest way to earn trust in a 4B model before anything depends
on it. Deferring the classifier would mean starting the eventual event study from zero
with an untested model at exactly the moment it first mattered.

### The 9b gate is an event study, not analyzer replay

Replacing the original step-8 gate. For each vault or Prime Access event in the
accumulated corpus: **did the linked slugs move in the predicted direction over 14 days,
against a matched control basket?** Sign test over the events available.

This needs no 90-day feature warmup, no analyzer, no signals table and no `harness.py`.
It tests the direction table directly, on the data that will actually exist. The original
analyzer hit-rate replay also had a statistical power problem the review named: Prime
Vault and Prime Access occur 4-6 times a year, so splitting hit rate by `event_type` **and**
`source` gives a per-cell n near 1 against 15+ free parameters. `sweep_thresholds()` being
cheap makes overfitting faster, not the gate stronger.

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
 (http)      (local,        (Ollama |     (local,     (4 tables)
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
  types.py          Article, Candidate, ExtractedEvent, ItemLink, EventType
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

### `news_candidates`

Added 2026-09-06. The review found a hole: article bodies are deliberately never
persisted, but the pipeline is asynchronous (`status='pending'` then `pending()` then
classify), so by the time the classifier ran there was **no text left to classify**.
Re-fetching is forbidden and storing bodies contradicts the design.

The gate's own output is the answer. It already produces exactly what the classifier
needs, and nothing more:

```
id, article_id FK,
slug, name,       -- the catalog item the gate matched
score,            -- fuzzy match score
context           -- the surrounding sentences, which IS the classifier prompt
UNIQUE (article_id, slug)
```

Written at gate time, before the article is queued. Bodies still never hit the database,
the async queue survives, and re-classification (a better model, a changed prompt) reads
stored contexts instead of re-fetching. This is what makes the per-candidate prompting
design actually implementable rather than merely described.

A consequence for `Article`: `content_hash` is a **stored field**, not a property
computed from `body`. An article loaded back from the database has `body=""`, so a
computed property could never match the stored hash and every re-fetch would look like an
edit.

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

### `vault_in` pre-effective behaviour: WORKING HYPOTHESIS, not established

**Status: unverified. Do not treat the current `vault_in` row as validated.** This is the
single most important thing for the 9b event study to test, and it is explicitly flagged
for review after roughly six months of data collection.

The review hypothesised that `vault_in` is sign-inverted where decay is 1.0, reasoning
that players mass-farm relics before a vault closes, so supply spikes and prices dip
before rising.

The user, who trades this market, offers a different model and labels it a **hypothesis,
not fact**. It has two regimes:

| Regime | Before `effective_at` | During the vault |
|---|---|---|
| **Vaulting is announced in advance** | Prices **jump up** on the announcement, as buyers accumulate specifically to resell at vault prices | Already elevated |
| **Vaulting is not pre-announced** | Prices sit **far lower**, no anticipation | Prices **rise through** the vault period as supply dries up |

Two consequences the design should be tested against, neither implemented yet:

1. **Selection effect.** The corpus only ever contains vaultings that were *announced*,
   because news is the only input. So every `vault_in` the pipeline sees is regime one by
   construction, which is the regime the current design models. That is convenient, but it
   also means the pipeline is blind to regime two rather than handling it.
2. **The decay curve may be backwards for regime two.** When announcement and effect
   coincide (a vaulting revealed by the patch that performs it), `anchor == published_at`,
   so the current model begins decaying immediately. The user's account says the move
   *builds* through the vault period instead. If that holds, `vault_in` needs a rising
   ramp after the anchor rather than exponential decay, which no other event type wants.

**Review trigger:** after ~6 months of parallel news and price accumulation, before any
bias is applied. Split the event study by whether announcement and effect were separated
by more than a day, and check the sign and the shape independently in each regime. Also
split relics from built parts, which the hypothesis does not distinguish and which the
review's farming-supply argument suggests may diverge.

### Which rank a link attaches to

`items.canonical_rank`, one link per item. Mods trade at ranks 0 through 10 and nothing
in an announcement identifies a rank, so news is treated as rank-agnostic and resolved
through the same field `feature_service.market_context` and `validation/harness.py`
already use. Rank 0 would starve maxed-mod signals; fanning across every rank would
multiply links and corrupt dedupe for no information gain.

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

## Fuzzy gate

Pure, stdlib only, in `wfm/news/match.py`. Scans article text left to right against a
lexicon built from the catalog, longest match wins and consumes its tokens. Set items
also register a base alias, so "Mesa Prime" reaches `mesa_prime_set` even though the
catalog name is "Mesa Prime Set".

### The single-token discriminator is ambiguity, not length

The first attempt required one-word item names to be four characters or longer. Querying
the catalog showed that drops 24 items, split almost evenly:

```
ordinary English:  Bite Bore Dig Flow Fury Howl Hunt Hush Jolt Maim Maul Rage Rush
Requiem mods:      Fass Haav Jahu Khra Lohk Norg Oull Ris Tink Vome Xata
```

The Requiem mods are distinctive nonsense syllables that should always match. The English
words are hopeless at any length. Length measured the wrong thing.

The rule instead: **a single-token match must be capitalised in the source text**, and a
token on the curated `_AMBIGUOUS_SINGLE_TOKENS` list must additionally **not be
sentence-initial**, because mid-sentence capitalisation is a strong proper-noun signal.
So `Xata` matches; `Rage` matches in "the Rage mod was buffed" but not in "Rage was the
theme of this update". The ambiguous list is data derived from the catalog once, not a
heuristic.

### Recall is measured, not assumed

The original design eyeballed precision during build step 2 and never measured recall,
which is worse: a false positive is visible in the output, a missed mention is invisible
by construction, and a 55%-recall gate silently makes every downstream number wrong. A
fixture test asserts a known set of mentions is found, and ingest reports per-run
candidate counts so a sudden drop is noticeable.

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
`confidence · weight` per group.

**Two details the review found underspecified, now pinned down.** Dedupe runs on the
**resolved slug**, not `subject_raw`, because "Mesa Prime" and "Prime Vault: Mesa Prime
Returns" are the same event across two sources and only linkage makes them comparable.
And a NULL `effective_at` groups with other NULLs for the same slug and event type rather
than forming its own singleton, since one source resolving a date and another not is the
common case, and treating them as distinct events is exactly the triple-counting `tanh`
would then hide rather than prevent.

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

## Build order

### Phase 9a, build now

Each step ships green and is independently useful. **Nothing here touches a Signal.**

1. `m0004` (four tables) + `NewsRepo` + types. Storage first, nothing reads it yet.
2. Sources + captured fixtures + `match.py` fuzzy gate, writing `news_candidates`.
   Ingest raw articles, classify nothing. Measure gate precision AND recall.
3. `Classifier` protocol + `FakeClassifier` + `OllamaClassifier` + `schema.py` + the
   label-to-number mapping. Then the 50-article benchmark to pick the model, with
   `ClaudeClassifier` brought forward if it is needed to produce the gold labels.
4. `link.py` set expansion + `news_item_links`, resolving rank via `items.canonical_rank`.
5. `news_service` ingest orchestration + daemon tick.
6. Frontend ES module split, then a **read-only** News tab: articles, their extracted
   events, and the items each event links to, with `link_method` badges.

At the end of 9a the corpus is accumulating, classifications are visible for inspection,
and nothing depends on them.

### Phase 9b, gated

Do not start until the event study has data, which is roughly six months of parallel
price and news accumulation.

7. **Event study.** For each vault / Prime Access event: did linked slugs move in the
   predicted direction over 14 days versus a matched control basket? Sign test.
   **This is the gate.** Tune the direction table and half-lives here, or cut.
8. `features/news.py` (decay, dedupe, saturation) + `NewsIndex`. Pure, heavily tested.
9. Bias post-pass in `analysis_service` + the sign-preservation property test.
10. News-only surfacing, the bias toggle, the evidence chips.

Step 7 before steps 8 to 10 is the same discipline as the original step-8 gate, moved to
a test that can actually run.

## Known holes carried into implementation

From the 2026-09-06 review, recorded rather than resolved.

- **`vault_in` direction and decay shape are an UNVERIFIED HYPOTHESIS.** Still the top
  open question, not resolved. The user's model has two regimes (announced vaultings rise
  on announcement; unannounced ones sit low and rise through the vault) and they label it
  hypothesis, not fact. The corpus only ever sees announced vaultings, so the pipeline is
  blind to regime two. The decay curve may also be backwards where announcement and effect
  coincide, since the move builds rather than decays. **Explicit 6-month review trigger:**
  see "vault_in pre-effective behaviour" above.
- **`as_of` leaks revised content.** `content_hash` re-fetch means stored text is always
  the newest revision, and DE edits hotfix posts after publication. Timing does not leak;
  content does. Fixing it properly needs revision-versioned articles, which `m0004` does
  not have. Acceptable for 9a (nothing replays yet); reassess before the event study.
- **The design names the second-order edge and does not build it.** "Which part of a
  vaulted set is the bottleneck" is called the fatter edge, then every set sibling gets a
  flat ~0.8 weight, which is the opposite of bottleneck detection. 9b should either build
  bottleneck detection (per-part volume and depth at announcement time) or stop claiming
  that edge.
- **Confidence-only caps the ceiling.** With `k_mag = 0` and no direction flip, the
  entire output is a dimmed confidence number, and the most valuable thing news can say
  ("this technical BUY is wrong, supply just flooded") is structurally forbidden. This
  was a deliberate choice, not an oversight, but it is worth revisiting once the event
  study says how trustworthy the classifications actually are.

## Open questions for implementation

- Exact Ollama model tag. Starting recommendation is Qwen3-4B-Instruct-2507 at Q8;
  confirm with a `pull` and settle the choice with the 50-article benchmark rather than
  by argument. Note the quantisation rationale in "Ollama backend" is overstated:
  distinguishing `vault_in` from `vault_out` from `prime_access` in DE's prose does need
  comprehension and domain knowledge, so let the benchmark decide, not the argument.
- Whether the three-label `strength` and `confidence` scales are granular enough. Start
  at three.
- Whether the curated frame-to-signature-weapon table is needed, or whether direct match
  plus set expansion is good enough. Decide after step 4 on real data.
- ~~Forum HTML parsing may need a fallback if the markup shifts.~~ **Resolved by the
  reconnaissance below: there is no forum HTML to parse.** The forum serves 403 to
  non-browser clients and its RSS serves the full post body, so the fragility is XML
  element names, not CSS selectors. The maintenance-cost question is answered too: the
  forum is the cheapest of the three sources, not the most expensive.
- Whether `news_enabled` stays off by default permanently.

## Source reconnaissance 2026-09-06

Captured live by `scripts/capture_news_fixtures.py`; fixtures in `tests/fixtures/news/`.
Everything below was observed, not assumed. **The headline result: none of the three
sources needs HTML parsing.** All three have a structured feed, so the "invent CSS
selectors" risk that gated this task is gone, except for one optional fallback.

### Summary

| | Feed | Body inline? | Requests per poll |
|---|---|---|---|
| warframe.com | JSON, paginated | no, teaser only | 1 + N articles |
| forums | RSS 2.0 | **yes, full HTML body** | 1 |
| reddit | Atom | **yes, full selftext** | 1 |

### warframe.com

1. **Structured feed: yes, JSON.** The listing page's own load-more handler calls
   `https://www.warframe.com/en/news/search_posts_json?query=&page=N&version=2`, which
   returns `{"posts": [...], "hasMore": bool}`, 10 posts per page. Every post is exactly
   `{date, title, description, url, image}` (verified as the key union across pages 1, 2
   and 5). `hasMore` was still true at page 5, reaching back to 2026-06-12, so historical
   backfill is available and cheap.
   The HTML listing is server-rendered and equally parseable (`.NewsCard-title`,
   `.NewsCard-date`, `.NewsCard-description`, `.NewsCard-tags`), so it stays as a
   fallback. **A regex-based fallback would be wrong**: the page carries an eleventh
   `NewsCard` inside `<script id="news-template" type="text/x-handlebars-template">` whose
   date is the literal `{{date}}`. An HTML parser skips script contents; a regex does not.
   The JSON payload has no platform tags, whereas the HTML has `.PlatformTag`. Nothing in
   9a reads tags, so this does not decide anything yet.
2. **`external_id`: the URL path slug**, e.g. `citrine-prime-access` from
   `https://www.warframe.com/en/news/citrine-prime-access`. Stable, no query string, no
   numeric id anywhere in the payload. Note `/news` 302s to `/en/news`, so the locale
   segment is in every canonical URL and must be stripped or fixed, not stored as-is.
3. **Publish date: `"2026-09-04 07:54:00"`, naive, and it is `America/Toronto`, not UTC.**
   Established rather than guessed: the Citrine Prime Access post is stamped 07:54:00,
   and the first three r/Warframe threads reacting to it are stamped 11:56:53, 12:00:13
   and 12:00:21 UTC. 07:54 + 4h = 11:54, two minutes before the first reaction. UTC would
   put the reactions four hours late; any other plausible offset is worse. DE is in
   London, Ontario, which agrees.
   **Use `zoneinfo("America/Toronto")`, not a fixed `-04:00`**, because the corpus will
   cross a DST boundary within weeks of starting.
   **Do not use the article page's `ld+json`.** It exists (`@type: NewsArticle`, with
   `datePublished` and `dateModified`) but for that same article it reads
   `2026-09-04 10:08:58`, also naive and consistent with neither reading of the listing
   date. It is a different clock; the listing date is the one corroborated by external
   evidence.
4. **Body: second request required.** The listing gives only `description`, a one-line
   teaser ("Wake up flawless on September 23."). The full body is on the article page in
   `div#post-body.BlogPost` inside `.ArticleBody-content.post-content`, server-rendered,
   no JS needed. Budget is therefore 1 + N. `/en/amp/<slug>` exists as a `<link>` but
   serves the identical 66KB document, so there is no lighter variant to fetch.
5. n/a.

### forums.warframe.com

1. **Structured feed: yes, RSS 2.0, and it is the only way in.** The HTML forum returns
   **403 to every non-browser client**, with the tool UA and with a current Chrome UA
   alike (5.6KB challenge page). The Invision per-forum feed at
   `https://forums.warframe.com/forum/3-pc-update-notes.xml/` returns **HTTP 200,
   `text/xml`, 874KB, 25 `<item>`s** with the plain tool UA. **The trailing slash is
   required.** `https://forums.warframe.com/rss/` also 403s, so the per-forum path is
   the one that works.
2. **`external_id`: `<guid isPermaLink="false">1520640</guid>`** — the bare numeric topic
   id, also embedded in `<link>` as `/topic/1520640-amir%E2%80%99s-shockwave-hotfix-4353/`.
   Prefer the `guid`: the link's slug half changes if a title is edited, the id does not.
3. **Publish date: `<pubDate>Tue, 18 Aug 2026 19:04:45 +0000</pubDate>`.** RFC 2822 with
   an explicit `+0000`. Aware, so `to_utc_iso` takes it directly. No assumption needed.
4. **Body: inline, in full.** `<description>` holds the entire post as CDATA-wrapped HTML
   (`<p>`, `<ul>`, `<strong>`). One request per poll, no fan-out. This is why the feed is
   874KB for 25 items: hotfix notes are long. **Practical consequence: the forum source
   is the cheapest of the three, not the most expensive**, which inverts the design's
   assumption that the forum would carry the highest maintenance cost.
5. n/a.

### reddit

1. **`.json` is dead; the Atom feed works.** `https://www.reddit.com/r/Warframe/hot.json`
   returns **403 with a 190KB HTML block page**, with the tool UA and with a Chrome UA
   alike. `old.reddit.com` redirects to a login wall. `https://www.reddit.com/r/Warframe/
   hot.rss?limit=25` returns **HTTP 200, `application/atom+xml`, 58KB, 25 `<entry>`s**
   with the plain tool UA. `search.rss` works the same way.
   **Reddit rate-limits hard**: a second identical request a few minutes later returned
   **429**. Whatever poll interval the ingest service picks, this source needs its own
   backoff, and reddit stays disabled by default as the design already says.
2. **`external_id`: `<id>t3_1vy9lck</id>`**, reddit's own fullname. Stable and opaque.
   The `<link href>` permalink carries a title slug and is the wrong choice.
3. **Publish date: `<published>2026-08-25T19:20:27+00:00</published>`.** ISO 8601, aware.
   There is a separate `<updated>` with the same value on a fresh post.
4. **Body: inline.** `<content type="html">` holds the entity-escaped selftext. One
   request per poll. Note the content is double-escaped HTML inside XML, so it needs
   unescaping once after XML parsing.
5. Answered above: **403 on JSON, 200 on Atom, 429 on repeat.**

### What this changes for the parser plan

- No HTML parsing is required for two of the three sources, and the third's HTML is only
  a fallback behind a JSON endpoint. The "confidently wrong selectors" risk that made
  this a reconnaissance gate is largely gone.
- Two of three parsers are XML feed readers with near-identical shape (fetch, iterate
  elements, map four fields). That is a shared `feedparser`-style helper on stdlib
  `xml.etree`, not three bespoke parsers. No new dependency.
- Only warframe.com needs a per-article fetch, so only it needs a fan-out budget.
- Only warframe.com needs a timezone assumption, and it is now recorded above with the
  evidence for it.

## Gate measurement 2026-09-06

Measured, not eyeballed, per "Recall is measured, not assumed". The real gate was run
over the captured fixtures against the live 3839-item catalog.

| Article | Candidates |
|---|---|
| Hotfix 43.5.3 (2.3KB) | 2 |
| Hotfix 43.5.4 (1.0KB) | 0 |
| Update 43.5 (37KB) | 56 |
| Devshorts #115 (1.9KB) | 0 |
| Dev Workshop: Banshee (6.2KB) | 4 |
| Citrine Prime Access (2.2KB) | 0, was 2 wrong ones before the guard |

**Three things this changes.**

1. **Prime Access announcements resolve to nothing, by construction.** The announced
   items do not exist in the catalog until release. Before the trailing-Prime guard the
   gate answered `steflos_set` and `corufell_set` for the Citrine Prime Access article,
   attaching the event to the base weapons. That is worse than silence, so the guard
   makes it silence. **`prime_access` is the event type the design leans on hardest, and
   the gate cannot see its subject on announcement day.** Resolving it needs either a
   catalog that carries unreleased items or a rule mapping "X Prime" to a future slug,
   and neither is in 9a. Recorded, not solved.
2. **Base frame names never resolve.** News prose says "Banshee", "Yareli", "Baruuk";
   the catalog sells "Banshee Prime Set". Recall on warframe-subject articles is
   therefore near zero unless the article says "Prime". Adding base-name aliases would
   fix recall and cost precision, since those words are common English in this domain.
   A decision for the user, not a silent default.
3. **One update note produced 56 candidates.** Per-candidate prompting means a big
   hotfix note is ~56 model calls. The classifier plan needs a per-article cap or a
   score floor; the cost belongs where it is spent, so it is not capped here.

## Gate decisions 2026-09-08

Findings 1 and 2 above are now decided (user, 2026-09-08). Finding 3 stays with plan 3.

### 1. Prime Access announcements: synthesize a set-level future slug

On seeing `"<Name> Prime"` with no catalog hit, the gate emits a candidate carrying the
**predicted set slug** (`"Steflos Prime"` → `steflos_prime_set`: lowercase, spaces to
underscores, append `_set`). That candidate existing is also what pulls the article into
the classifier queue, so the `prime_access` event and its `date_text` are recorded even
if the slug guess later proves wrong.

- **Set level only.** Part slugs are not predictable (barrel/receiver/stock, blade/handle,
  blueprint/chassis/neuroptics/systems, and DE's market names occasionally differ). The
  set is one clean prediction and it is all the 9b event study needs.
- **`link_method = 'synthetic'`.** Direction cannot come from `Item.tags` (no catalog
  row), so synthetic links assume `role = set` (true by construction) → `prime_access`
  row → `down`, weight 1.0.
- **Check before synthesizing.** If `<name>_prime_set` already exists (Prime released
  between announcement and ingest), link normally instead.
- **Reconciliation pass.** On catalog refresh (`refresh-items`), re-run linkage for any
  event still holding a synthetic link. Once the real slug exists, replace the synthetic
  link with a real one and run normal set-expansion to parts and relics. This is the
  design's re-runnable linkage (`replace_links_for`) with a trigger.
- **Staleness flag.** If the slug has not appeared within ~30 days of a known release, the
  News tab shows the event as unresolved rather than trusting the guess indefinitely.

**Plan 3 must check** whether `news_candidates` / `news_item_links` carry a FK to
`items(slug)` in `m0004`. A synthetic slug would violate it; if so, drop the FK or add a
nullable `is_synthetic` flag.

### 2. Base warframe names: alias to the Prime set, reusing the single-token discriminator

Add every base warframe name to the lexicon, aliased to `<name>_prime_set`.

- **Guard 1: alias resolves only if `<name>_prime_set` exists in the catalog.** Frames
  with no Prime yet (Dagath, Qorvex, Kullervo, …) simply do not link. No synthesis here,
  unlike decision 1: a base frame has no announced release date to reconcile against.
- **Guard 2: the existing single-token rule applies unchanged.** The English-collision
  names (`Ember`, `Frost`, `Volt`, `Mag`, `Nova`, `Ash`, plus any a catalog pass turns
  up) join `_AMBIGUOUS_SINGLE_TOKENS`, so they also require non-sentence-initial position.
- **`link_method = 'base_alias'`, weight ≈ 0.9** (a bare name is weaker evidence than an
  explicit "Banshee Prime").
- Precision on non-event mentions ("Frost damage was adjusted") is the classifier's job:
  such a mention gets an `event_type` that yields `sign = 0`. The gate stays recall-first.

**Scope: warframes only.** Weapons ("Braton" → Braton Prime) have far more non-Prime
entries and worse ambiguity; a separate decision if the corpus later shows it matters.

## Triage measurement 2026-09-09

Measured on the live 3839-item catalog against the Update 43.5 fixture
(`tests/fixtures/news/forums_updates.xml`, entry index 2, "Update 43.5: Amir's
Shockwave", 37280 body chars after `strip_tags`), the 37KB note that produced the
56-candidate problem. Reproduce: build the lexicon from `items`, run
`find_candidates(title + "\n\n" + body, lexicon)`, then `triage(candidates, cap=25)`.

**The answer key.** Reading the note by hand, 36 of the 56 candidates carry a real,
tradeable event:

- 27 mods in the "Permanent Cred Offerings" list (19 auras: Corrosive Projection ...
  Steel Charge; 8 warframe mods: Deceptive Bond ... Singularity). The section says they
  "are now available at all times in the Cred Offerings Store — in other words, they are
  no longer part of the store rotations": a permanent supply increase, and the most
  price-relevant thing in the whole note.
- 4 weapon arcanes added to the rotation under "New Cred Offerings" (Biotic Rounds,
  Leaded Gas, Sentient Surge, Vile Discharge), plus Clip Delegation, whose duplicate was
  removed from that rotation.
- 4 newly introduced reward mods (Prototype Shock Coils / EFV-8 Mars, Overpressured
  Rounds / EFV-5 Jupiter).

The other 20 are bug-fix lines, Nightwave act names that collide with mod names
(`fury`, `guardian`, `sanctuary`, `bounty_hunter`, `howl`, `hunt`, `reach`, `twitch`),
and two skin-list mentions whose subject is a skin, not the weapon (`cedo_set`,
`vesper_77_set`).

### As shipped (six groups, cap 40)

| | Candidates | Real events kept (of 36) |
|---|---|---|
| Gate output | 56 | 36 |
| After the signal filter | 28 | 20 |
| After the cap (40) | 28 | 20 |

Signal-group hits over all 56: vault 1, release 0, balance 6, drop 0, rework 0,
**supply 21**. Recall 20/36; precision 20/28. Cost: 56 -> 28 calls, a 50% reduction.

**The cap does not engage**, which is the intent: triage does the filtering and the cap
is a backstop against a pathological article. It is 40, not the 25 the plan first named,
because 25 was chosen before anyone had run the filter over a real article and the
largest article in the corpus leaves 28 survivors. At 25 the cap was measured cutting
three of them, and because every survivor scores signal 1 the tie-break decided which:
the two lowest gate scores (`kill_switch` 0.90, `secondary_wind` 0.89, both noise) and
then the alphabetically last of the tied 1.0s, `vile_discharge` — a real event, lost to
alphabetical order. That is precisely the arbitrary N-of-M choice the plan rejected when
it refused a cap-only design. 40 restores the plan's stated intent, a ceiling rather
than a guillotine.

**The precision cost, stated plainly.** 8 of the 28 kept candidates (29%) carry no
event: `bounty_hunter`, `fury`, `guardian`, `primed_chamber`, `sanctuary`, `kill_switch`,
`secondary_wind`, `vesper_77_set` — Nightwave act names that collide with mod names,
bug-fix lines, and one skin-list mention. `"rotation"` and `"cred offerings"` are broad
keywords and this is the noise they buy. It is the right trade and it is not free: a
wasted model call costs one call, a missed event is invisible forever, so triage is
deliberately biased toward keeping. Budget on this article is ~29% noise.

**Reading `skipped` on a store-update note.** Every survivor here scores signal 1, so
the ordering inside the survivor set is decided by gate score then slug — close to
alphabetical. That means a non-zero `skipped` on an article of this shape indicates the
article was *under-read*, not that noise was removed: whatever the cap cut was tied with
what it kept. Task 13's reporting should treat `skipped > 0` on a store update as a
flag, not as a success metric.

**Real events still dropped: 16.** All of them bare entries in the middle of the
"Permanent Cred Offerings" bullet list (`deceptive_bond`, `power_of_three`,
`prism_guard`, `purging_slash`, `purifying_flames`, `recharge_barrier`,
`rifle_scavenger`, `rumbled`, `shield_disruption`, `shotgun_scavenger`, `singularity`,
`sniper_scavenger`, `sprint_boost`, `steel_charge`) plus the two new-reward mods
(`prototype_shock_coils`, `efv_8_mars_set`). **No keyword can reach them**: their
200-character context window contains only other mod names, no prose at all. See the
known hole below.

### Before `supply` existed (five groups), and why the group exists

The first pass measured the same article with the original five groups: 56 -> **7**
survivors, the cap never engaging, **recall 2/36** and precision 2/7. Only
`overpressured_rounds` and `efv_5_jupiter_set` survived, and only because their windows
happened to contain "decrease Spread"; every candidate in both Cred Offerings lists
scored zero. An 87% call reduction that loses 34 of 36 real events is not a cost
control, it is a mute button, and a store-update note is exactly where bulk supply
events live. The plan pre-committed to the remedy — when recall measures badly the fix
is data, not architecture — so a sixth `supply` group was added rather than a redesign.
It moved recall 2/36 -> 20/36 and precision 2/7 -> 20/28, at the price of 21 more model
calls on this note.

Two group-membership choices worth recording: `"now available"` moved from `release` to
`supply` (it is an availability claim, and one phrase scoring under two groups would
count a single piece of evidence twice), and the bare `"no longer"` was deliberately
not added to `supply` for the same reason — it already scores under `balance`, so it
costs nothing in recall to leave it there.

### Known hole: context width, deferred past plan 3

16 of the 36 real events on Update 43.5 are unreachable by any keyword, because their
200-character context window contains no prose — the gate cut them out of the middle of
a 30-name bullet list. Fixing that means widening or making section-aware
`find_candidates(context_chars=...)`, which is a gate change, not a triage one: `context`
is captured and stored at ingest, so the 358 candidates already in the database were all
cut at the current width and would need a re-ingest to benefit. That is a data change
and deserves its own argument. Deferred past plan 3, with the number recorded here so it
does not have to be rediscovered.
