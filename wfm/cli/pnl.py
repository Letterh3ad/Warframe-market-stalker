from __future__ import annotations

import sys

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.cli.timeframe import parse_since
from wfm.services import ledger_service


def register(parser) -> None:
    parser.add_argument("--realized", action="store_true", help="skip open positions")
    parser.add_argument("--since", default=None, help="ISO date, or a duration like 7d")
    parser.set_defaults(handler=run)


def run(args) -> int:
    try:
        since = parse_since(args.since)
    except ValueError as exc:
        print(f"bad --since {args.since!r}: {exc}", file=sys.stderr)
        return 1
    ctx = context_factory.build(args)
    emit(ledger_service.pnl(ctx, since=since, realized_only=args.realized), args.json)
    return 0
