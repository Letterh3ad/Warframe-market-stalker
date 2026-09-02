from __future__ import annotations

import sys

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.cli.timeframe import parse_since
from wfm.services import alert_service


def register(parser) -> None:
    parser.add_argument("--since", default=None, help="ISO date, or a duration like 7d")
    parser.add_argument("--analyzer", default=None)
    parser.add_argument("--slug", default=None)
    parser.add_argument("--limit", type=int, default=50)
    parser.set_defaults(handler=run)


def run(args) -> int:
    try:
        since = parse_since(args.since)
    except ValueError as exc:
        print(f"bad --since {args.since!r}: {exc}", file=sys.stderr)
        return 1
    ctx = context_factory.build(args)
    if args.json:
        emit(
            alert_service.list_signals(
                ctx, since=since, analyzer=args.analyzer, slug=args.slug, limit=args.limit
            ),
            True,
        )
        return 0
    print(
        alert_service.render_signals(
            ctx, since=since, analyzer=args.analyzer, slug=args.slug, limit=args.limit
        )
    )
    return 0
