"""Which classifier backend should this project use? Measure, do not guess.

Not a test and not collected by pytest (scripts/ is outside testpaths). The corpus is
the stored news_candidates, not article bodies: a candidate's stored context is
exactly what a classifier is fed in production, so scoring on candidates measures the
real task rather than a proxy for it.

Four subcommands, each reading and writing JSONL so a run can be resumed and a gold
set hand-edited in a text editor:

  export --db wfm_market.db --n 50 --out gold_input.jsonl
      Sample N candidates round-robin across articles, with each article's
      published_at, from the live database (opened read-only).
  label --in gold_input.jsonl --out gold.jsonl --model claude-opus-5
      Run the Claude backend once to produce the gold set. The only step that
      costs money. Opus, not Haiku: the gold set is the ruler every other
      backend is measured against, so its errors become permanent measurement
      error and it should be the best model available.
  run --in gold_input.jsonl --out <name>.jsonl --backend ollama --model <tag>
      The same inputs through a candidate backend.
  score --gold gold.jsonl --got <name>.jsonl
      Print the per-field agreement table.

Run by hand: python scripts/news_benchmark.py export ...
Never invoked by the test suite. `label` and the claude backend of `run` need the
optional `anthropic` extra and an API key; `run --backend ollama` needs a running
Ollama server. Importing this module needs neither.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path

from wfm.news.classify.base import ClassifierError
from wfm.news.dates import resolve_effective_at
from wfm.news.schema import decode
from wfm.news.types import ClassifyLabels, ClassifyRequest

EXACT_FIELDS = ("event_type", "direction")

# Ordinal scales, so a neighbouring label is a near miss rather than a failure. An
# exact-match score here would report noise: the difference between "moderate" and
# "major" is a judgement call two careful humans also disagree about.
WITHIN_ONE = {
    "strength": ("minor", "moderate", "major"),
    "confidence": ("low", "medium", "high"),
}

DATE_TOLERANCE = timedelta(days=1)

# A bare-month date ("September 20") needs an anchor year to resolve at all, so
# scoring without one would collapse every date to None and report perfect agreement
# between "September 20" and "October 20". The real publish dates live in
# gold_input.jsonl and the `score` subcommand passes them per key; this stands in
# when score() is called with bare label dicts, which is what the unit test does.
_DEFAULT_ANCHOR = datetime(2026, 9, 6, tzinfo=timezone.utc)


def _within_one(scale: tuple[str, ...], a: str, b: str) -> bool:
    return abs(scale.index(a) - scale.index(b)) <= 1


def _effective(labels: ClassifyLabels, anchor):
    return resolve_effective_at(labels.timing, labels.date_text, anchor)


def score(gold: dict, got: dict, anchors: dict | None = None) -> dict:
    """Per-field agreement against the gold set.

    A key the backend never answered scores zero rather than being skipped: a
    backend that fails half the corpus is not a backend that is right about the half
    it answered, and skipping would flatter exactly the failure mode that matters.
    """
    anchors = anchors or {}
    total = len(gold)
    hits = {name: 0 for name in (*EXACT_FIELDS, *WITHIN_ONE, "effective_at")}
    missing = 0

    for key, want in gold.items():
        have = got.get(key)
        if have is None:
            missing += 1
            continue
        for name in EXACT_FIELDS:
            hits[name] += getattr(want, name) == getattr(have, name)
        for name, scale in WITHIN_ONE.items():
            hits[name] += _within_one(scale, getattr(want, name), getattr(have, name))

        anchor = anchors.get(key, _DEFAULT_ANCHOR)
        want_at, have_at = _effective(want, anchor), _effective(have, anchor)
        if want_at is None and have_at is None:
            hits["effective_at"] += 1
        elif want_at is not None and have_at is not None:
            hits["effective_at"] += abs(want_at - have_at) <= DATE_TOLERANCE

    result = {name: (count / total if total else 0.0) for name, count in hits.items()}
    result["n"] = total
    result["missing"] = missing
    return result


# --- JSONL plumbing -----------------------------------------------------------
#
# `effective_at` is scored on the resolved datetime, not on the date_text string:
# that measures the value that actually reaches the database, and it gives a model
# credit for writing "September 20th" where the gold set says "September 20". Label
# rows carry the source published_at through unchanged so `score` can rebuild the
# same anchor the classifier saw, rather than resolving against nothing.


def _read_jsonl(path: str | Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: str | Path, rows) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _round_robin(groups):
    iterators = [iter(g) for g in groups]
    while iterators:
        for it in list(iterators):
            try:
                yield next(it)
            except StopIteration:
                iterators.remove(it)


# --- export ---------------------------------------------------------------


def cmd_export(args: argparse.Namespace) -> None:
    # Read-only: wfm_market.db is the user's live data and this script has no
    # business writing to it.
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT c.article_id, c.slug, c.name, c.context, a.published_at "
            "FROM news_candidates c JOIN news_articles a ON a.id = c.article_id "
            "ORDER BY c.article_id, c.slug"
        ).fetchall()
    finally:
        conn.close()

    by_article: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        by_article.setdefault(row["article_id"], []).append(row)

    sampled = islice(_round_robin(by_article.values()), args.n)
    _write_jsonl(
        args.out,
        (
            {
                "key": f"{row['article_id']}:{row['slug']}",
                "subject": row["name"],
                "context": row["context"],
                "published_at": row["published_at"],
            }
            for row in sampled
        ),
    )


# --- label / run: send gold_input.jsonl through a real backend -------------


def _to_request(row: dict) -> ClassifyRequest:
    published = row.get("published_at")
    return ClassifyRequest(
        subject=row["subject"],
        context=row["context"],
        published_at=datetime.fromisoformat(published) if published else None,
    )


async def _classify_rows(rows: list[dict], classifier) -> list[dict]:
    out = []
    for row in rows:
        try:
            labels = await classifier.classify(_to_request(row))
        except ClassifierError as exc:
            # No line is written for this key: score() treats an absent key as
            # "missing", which is the honest outcome for a backend that fell over.
            # Silently downgrading it to a wrong label would hide the failure.
            print(f"warning: {row['key']}: {exc}", file=sys.stderr)
            continue
        out.append(
            {
                "key": row["key"],
                "published_at": row.get("published_at"),
                "event_type": labels.event_type,
                "direction": labels.direction,
                "strength": labels.strength,
                "confidence": labels.confidence,
                "timing": labels.timing,
                "date_text": labels.date_text,
                "rationale": labels.rationale,
            }
        )
    return out


def cmd_label(args: argparse.Namespace) -> None:
    # Imported here, not at module level, so importing this module never requires
    # the optional `anthropic` extra.
    from wfm.news.classify.claude import ClaudeClassifier

    rows = _read_jsonl(args.infile)

    async def go():
        async with ClaudeClassifier(model=args.model) as clf:
            return await _classify_rows(rows, clf)

    _write_jsonl(args.out, asyncio.run(go()))


def cmd_run(args: argparse.Namespace) -> None:
    rows = _read_jsonl(args.infile)

    async def go():
        if args.backend == "ollama":
            from wfm.news.classify.ollama import OllamaClassifier

            async with OllamaClassifier(args.model, base_url=args.ollama_url) as clf:
                return await _classify_rows(rows, clf)
        from wfm.news.classify.claude import ClaudeClassifier

        async with ClaudeClassifier(model=args.model) as clf:
            return await _classify_rows(rows, clf)

    results = asyncio.run(go())
    _write_jsonl(args.out, results)


# --- score ------------------------------------------------------------------


def cmd_score(args: argparse.Namespace) -> None:
    gold_rows = _read_jsonl(args.gold)
    got_rows = _read_jsonl(args.got)

    gold = {row["key"]: decode(row) for row in gold_rows}
    got = {row["key"]: decode(row) for row in got_rows}
    anchors = {
        row["key"]: datetime.fromisoformat(row["published_at"])
        if row.get("published_at")
        else None
        for row in gold_rows
    }

    result = score(gold, got, anchors=anchors)
    _print_report(result, args.gold, args.got)


def _print_report(result: dict, gold_path: str, got_path: str) -> None:
    n = result["n"]
    missing = result["missing"]
    fields = [k for k in result if k not in ("n", "missing")]
    width = max(len(f) for f in fields)

    print(f"gold: {gold_path}  got: {got_path}")
    print(f"n={n}  missing={missing} (no answer at all, scored 0 on every field)")
    if n < 30:
        print(f"NOTE: n={n} is small; treat differences under a few points as noise.")
    print()
    for name in fields:
        hits = round(result[name] * n)
        print(f"{name:<{width}}  {result[name]:.1%}  ({hits}/{n})")


# --- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="sample candidates from the live DB")
    p_export.add_argument("--db", default="wfm_market.db")
    p_export.add_argument("--n", type=int, default=50)
    p_export.add_argument("--out", required=True)
    p_export.set_defaults(func=cmd_export)

    p_label = sub.add_parser("label", help="produce the gold set with Claude")
    p_label.add_argument("--in", dest="infile", required=True)
    p_label.add_argument("--out", required=True)
    p_label.add_argument("--model", default="claude-opus-5")
    p_label.set_defaults(func=cmd_label)

    p_run = sub.add_parser("run", help="run a candidate backend over the same inputs")
    p_run.add_argument("--in", dest="infile", required=True)
    p_run.add_argument("--out", required=True)
    p_run.add_argument("--backend", choices=("ollama", "claude"), required=True)
    p_run.add_argument("--model", required=True)
    p_run.add_argument("--ollama-url", default="http://localhost:11434")
    p_run.set_defaults(func=cmd_run)

    p_score = sub.add_parser("score", help="print per-field agreement against gold")
    p_score.add_argument("--gold", required=True)
    p_score.add_argument("--got", required=True)
    p_score.set_defaults(func=cmd_score)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
