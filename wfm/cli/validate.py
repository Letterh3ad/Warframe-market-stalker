from __future__ import annotations

import sys

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import validation_service


def register(parser) -> None:
    parser.add_argument("--analyzer", default=None, help="default: every enabled analyzer")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--horizon-days", type=int, default=7)
    parser.add_argument("--sweep", default=None, help="threshold key to sweep")
    parser.add_argument("--values", default=None, help="comma-separated values for --sweep")
    parser.set_defaults(handler=run)


def run(args) -> int:
    ctx = context_factory.build(args)
    try:
        rows = validation_service.validate(
            ctx,
            analyzer=args.analyzer,
            start=args.start,
            end=args.end,
            horizon_days=args.horizon_days,
            sweep_key=args.sweep,
            sweep_values=[float(v) for v in args.values.split(",")] if args.values else None,
        )
    except (KeyError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.json:
        emit(rows, True)
    else:
        _print_human(rows)
    return 0


def _print_human(rows: list[dict]) -> None:
    for row in rows:
        head = row.get("analyzer", "?")
        if "z_threshold" in row or any(k not in _RESULT_KEYS for k in row):
            swept = {k: v for k, v in row.items() if k not in _RESULT_KEYS}
            head = f"{head} {swept}"
        print(
            f"{head}: signals={row['signals']} hit_rate={_fmt(row['hit_rate'])} "
            f"median_fwd={_fmt(row['median_forward_return'])}"
        )
        for direction, stats in sorted(row.get("by_direction", {}).items()):
            print(f"    {direction}: {stats['hits']}/{stats['signals']}")
        if row.get("failures"):
            print(f"    failed: {row['failures']} ({', '.join(row['failed_slugs'])})")


_RESULT_KEYS = {
    "analyzer", "signals", "hits", "hit_rate", "median_forward_return", "by_direction",
    "failures", "failed_slugs",
}


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:.3f}"
