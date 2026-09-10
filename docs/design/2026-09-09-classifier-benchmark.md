# Classifier model benchmark, 2026-09-09 (run 2026-09-10)

## Read this first: accuracy was never measured

**Nothing in this document says any model is correct.** The benchmark the plan
originally specified builds a gold set with `claude-opus-5`, hand-corrects it, and
scores local models against it. The user chose not to spend on the Anthropic API, so
**no gold set exists, `scripts/news_benchmark.py label` was never run, and no Claude
backend call was made at any point.**

Without a gold set there is no ruler. Every number below measures *operational
fitness* — does the model load in 8GB, does it honour the output schema, how long does
a call take, does it repeat itself — and **none of them measures whether a label is
right.** The model named at the bottom is a **provisional operational default**, not a
winner of a comparison. Treat it as "the one thing we confirmed can run the job", not
as "the one thing we confirmed does the job well."

Concretely, this document does **not** contain, and you must not infer from it:

- any per-field accuracy figure,
- any confusion analysis of which event types get mixed up,
- any comparison against `claude-haiku-4-5` or any other Claude model,
- any answer to the spec's 4B-at-Q8 vs 8B-at-Q4 argument (that is an accuracy
  question and is untouched),
- any evidence that `news_enabled` should move off `false`.

## Corpus

50 candidates sampled round-robin across 27 articles, from the 358 candidates stored
in `wfm_market.db` (opened read-only, `file:...?mode=ro`). Mean stored context length
381 characters. A candidate's stored context is exactly what the classifier is fed in
production, so this is the real task and not a proxy.

## Hardware and environment

- RTX 5060 Laptop, 8 GB VRAM. Windows 11.
- Ollama 0.32.3, started for this session
  (`C:\Users\lette\AppData\Local\Programs\Ollama\ollama.exe serve`), serving
  `localhost:11434`.
- `.venv/Scripts/python.exe`.

## The model, and its real registry tag

**`qwen3:4b-instruct-2507-q8_0`** — confirmed by an actual successful
`ollama pull`, not from memory. 4.3 GB download, Q8_0, 4.0B parameters, qwen3 family.

It is the non-Thinking Instruct variant, which is what the plan requires. One
caveat worth recording: `/api/show` reports `capabilities: ["tools","thinking",
"completion"]` for this tag. That is Ollama's family-level capability guess, not a
property of the `-instruct-2507-` weights; the tag carries no `-Thinking-` marker, and
under a `format` schema the model cannot emit a reasoning block anyway. If a later
reader is choosing tags, do not read that `thinking` string as disqualifying this one.

**VRAM fit: confirmed.** With the model warm, `ollama ps` reports:

```
NAME                           SIZE      PROCESSOR    CONTEXT
qwen3:4b-instruct-2507-q8_0    5.0 GB    100% GPU     4096
```

100% GPU, no CPU spill, at the default 4096-token context. Prompts on this corpus
(system prompt plus a ~381-character context) are far under that.

## What was measured

### Schema-constrained output reliability — 50/50 (100%)

Every one of the 50 calls returned output that `wfm.news.schema.decode()` accepted.
Zero `ClassifierError`s; `scripts/news_benchmark.py run` wrote nothing to stderr and
produced 50 of 50 output lines. Re-running `decode()` over the written file
independently also passed 50/50.

Sample size 50. This is the single most useful number here: a model that cannot honour
the schema is unusable regardless of accuracy, and this one honoured it completely on
this corpus. It says nothing about whether the values inside the schema were right.

### Latency — 2.12 s per call (n=50, warm)

Two full passes over the same 50 candidates, with the model already loaded:

| Pass | Wall clock | Per call |
|---|---|---|
| 1 | 105.92 s | 2.12 s |
| 2 | 105.34 s | 2.11 s |

Measured separately, after unloading the model: **cold load plus one call = 7.4 s**, so
the first call of a backfill costs about 5 s more than a warm one, once, not per
article.

Operational consequences at 2.12 s/call:

- The largest real article measured in Task 8 leaves **28 candidates after triage**:
  **~59 s** to classify that article.
- The stored corpus averages **13.3 candidates per article** (358 across 27):
  **~28 s** for a typical article.
- Classifying **all 358 stored candidates: ~12.6 minutes.** The plan's stated worry
  was whether a local backfill is an overnight or a weekend job. On this corpus it is
  neither; it is a coffee break.

### Determinism — byte-identical across two runs

The two passes above produced **byte-identical JSONL files**, including free-text
`rationale`. Scored against each other via the harness, all five scored fields agree
50/50. At `temperature: 0` this backend is reproducible, which means a future gold-set
comparison can be run against a stored output file rather than re-run.

This is repeatability, **not** accuracy. A model that is confidently and consistently
wrong scores 100% here.

### Inter-model agreement — NOT measured, because no second model fits

Checked against the locally present library:

| Model | Size | Verdict |
|---|---|---|
| `gemma4:12b` | 8.9 GB resident | **Does not fit.** `ollama ps` reports `33%/67% CPU/GPU`. Spills to CPU; a single cold call took 40.5 s. Not benchmarked as a candidate. |
| `qwen3-coder:30B` | 18.6 GB | Does not fit. Not run. |
| `qwen2.5:32b`, `qwen-logic` | 19.9 GB | Do not fit. Not run. |
| `orcarouter/Qwen3.8-27B-Uncensored` | 17.7 GB, and `thinking` | Does not fit, and is a thinking model. Not run. |
| `qwen2.5-coder:14b`, `qwen-coder` | 9.0 GB | Coder models, and over budget. Not run. |
| `general-heretic:latest` | 4.3 GB, qwen3 4.0B Q8_0 | Fits, but its Modelfile is `TEMPLATE {{ .Prompt }}` — **no chat template at all**, so `/api/chat` would send it unroleplayed text. Not a valid comparison. Not run. |

The brief warned `gemma4:12b` might not exist. It does exist locally; it simply does
not fit in 8 GB. So no genuine second model was available, and **no agreement number is
reported.** Note for anyone tempted to add one later: agreement is not accuracy. Two
models can agree and both be wrong.

## What the output looks like, without judging it

These are distributions and internal-consistency observations over the 50 outputs.
They are descriptive. Do not read them as error rates — there is no ruler.

- `event_type`: other 37, buff 6, prime_access 3, new_content 1, nerf 1, vault_out 1,
  rework 1.
- `direction`: unclear 40, up 6, down 4.
- `strength`: minor 40, moderate 7, major 3.
- `confidence`: **high 45**, low 4, medium 1.
- `timing`: immediate 31, unknown 13, dated 6.

**The confidence distribution is worth flagging.** 45 of 50 answers are `high`,
including a large majority of the 37 `other` answers. That is the small-model
overconfidence the design anticipated when it chose coarse labels over floats. Whether
that overconfidence is *misplaced* is exactly the thing a gold set would tell us and
this run cannot.

**One concrete, ruler-free defect: `date_text` ignores its instruction.** The system
prompt says "the date exactly as the text writes it, or null. Never compute a date."

- 37 of 50 answers carry a non-null `date_text`.
- **37 of those 37 are ISO dates** (`2026-09-04`), a format that appears nowhere in
  any article body.
- **0 of 37 appear verbatim in the candidate's own context.**
- Every one of them equals the article's `Published:` header date. The model is
  copying the header, not quoting the article.

This is checkable by string comparison, not by judgement, which is why it belongs in a
document with no gold set.

Its operational impact is smaller than it looks, because `resolve_effective_at()`
ignores `date_text` entirely when `timing == "immediate"` (31 rows) and when
`date_text` is null (13 rows). It bites on the 6 `dated` rows, where `effective_at`
resolves to the publish date rather than to a real named date. Since the publish date
is what `immediate` would have produced anyway, and a missing `effective_at` already
degrades to `published_at` in the bias query, the failure lands on the safe side. But
it does mean **the forward-dated pathway never fired once on this corpus.**

Related, and stated with its tiny sample size attached: exactly **1 of the 50**
contexts actually contains a written month-and-day date ("September 9", key
`6:plains_of_eidolon_scene`), and on that one the model answered `timing: unknown`,
`date_text: null`. n=1. That is one observation, not a rate, and it is only worth
recording because it is the only case in the corpus where the date pathway could have
worked.

## How to reproduce this

```bash
# 1. Start Ollama
"C:\Users\lette\AppData\Local\Programs\Ollama\ollama.exe" serve
curl http://localhost:11434/api/tags     # must answer

# 2. Pull the model (4.3 GB)
ollama pull qwen3:4b-instruct-2507-q8_0

# 3. Sample the corpus from the live DB, read-only
.venv/Scripts/python.exe scripts/news_benchmark.py export \
    --db wfm_market.db --n 50 --out gold_input.jsonl

# 4. Run it (twice, to confirm determinism)
.venv/Scripts/python.exe scripts/news_benchmark.py run \
    --in gold_input.jsonl --out run1.jsonl \
    --backend ollama --model qwen3:4b-instruct-2507-q8_0
.venv/Scripts/python.exe scripts/news_benchmark.py run \
    --in gold_input.jsonl --out run2.jsonl \
    --backend ollama --model qwen3:4b-instruct-2507-q8_0

# 5. Determinism check (this is self-agreement, NOT accuracy)
.venv/Scripts/python.exe scripts/news_benchmark.py score \
    --gold run1.jsonl --got run2.jsonl

# 6. VRAM fit, while the model is still resident
ollama ps
```

Cold-load latency was taken by unloading first
(`curl .../api/generate -d '{"model":"...","keep_alive":0}'`) and timing a single call.

Latency was taken as wall clock around step 4 divided by 50. Schema reliability is
`wc -l run1.jsonl` (a failed call writes no line and warns on stderr) cross-checked by
re-`decode()`ing the file.

The harness was used as built in Task 16 and was not modified.

## Provisional operational default

**`news_classifier = "ollama"`, `news_model = "qwen3:4b-instruct-2507-q8_0"`.**

Set these in your local `wfm.toml` (gitignored, so it is not part of this commit):

```toml
news_classifier = "ollama"
news_model = "qwen3:4b-instruct-2507-q8_0"
news_ollama_url = "http://localhost:11434"
news_enabled = false
```

**Justification, and it is entirely operational:** it is the only model confirmed to
load fully on 8 GB of VRAM, honour the constrained-decoding schema on 50 of 50 real
stored candidates, answer in ~2 s, and reproduce itself byte for byte. Nothing here
argues it labels well. It is the default because it *runs*, and because a default that
runs is more useful than no default while the accuracy question waits for a budget.

**`news_enabled` stays `false`**, as the plan requires, until the 9b backtest. Nothing
in this plan moves a bias or a Signal, and nothing in this document is a reason to
change that.

## What would be needed to validate this

The full benchmark this document declined to run:

1. `scripts/news_benchmark.py label --in gold_input.jsonl --out gold.jsonl --model
   claude-opus-5` over the same 50 inputs (~50 Opus calls; needs `pip install
   anthropic` and a credential).
2. Hand-correct every line of `gold.jsonl` against its context. The count of corrected
   lines is itself the headline result for the Claude backend.
3. `score --gold gold.jsonl --got run1.jsonl`. Because this backend is deterministic,
   `run1.jsonl` from *this* session can be scored directly — the local run does not
   need repeating.
4. Then, and only then: the spec's 4B-Q8 vs 8B-Q4 question, a `claude-haiku-4-5`
   comparison, and a confusion analysis of which event types get mixed up and whether
   the mistakes are safe (`other`/`unclear`) or unsafe (a confident wrong direction).

Until step 3 exists, the phrase "chosen model" should not be used about this project.
