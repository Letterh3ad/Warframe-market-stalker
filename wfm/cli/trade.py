from __future__ import annotations

import sys

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import ledger_service


def register(parser) -> None:
    sub = parser.add_subparsers(dest="side", required=True)
    for side in ("buy", "sell"):
        p = sub.add_parser(side)
        p.add_argument("query")
        p.add_argument("quantity", type=int)
        p.add_argument("platinum", type=int)
        p.add_argument("--rank", default=None)
        p.add_argument("--note", default=None)
    parser.set_defaults(handler=run)


def run(args) -> int:
    ctx = context_factory.build(args)
    try:
        result = ledger_service.record(
            ctx, args.side, args.query, quantity=args.quantity, platinum=args.platinum,
            rank=args.rank, note=args.note,
        )
    except (LookupError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    emit(result, args.json)
    return 0
