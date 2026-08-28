from __future__ import annotations

import sys

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import watch_service


def register(parser) -> None:
    sub = parser.add_subparsers(dest="watch_command", required=True)

    add = sub.add_parser("add")
    add.add_argument("query")
    add.add_argument("--rank", default=None, help="rank number, or 'all'")
    add.add_argument("--pin", type=float, default=0.0)
    add.add_argument("--alert", action="store_true", help="force live alerts for this item")

    rm = sub.add_parser("rm")
    rm.add_argument("query")
    rm.add_argument("--rank", default=None)

    sub.add_parser("ls")

    suggest = sub.add_parser("suggest")
    suggest.add_argument("--top", type=int, default=20)

    parser.set_defaults(handler=run)


def run(args) -> int:
    ctx = context_factory.build(args)
    try:
        if args.watch_command == "add":
            emit(
                watch_service.add(ctx, args.query, rank=args.rank, pin=args.pin, alert=args.alert),
                args.json,
            )
        elif args.watch_command == "rm":
            emit(watch_service.remove(ctx, args.query, rank=args.rank), args.json)
        elif args.watch_command == "ls":
            emit(watch_service.list_(ctx), args.json,
                 columns=["slug", "rank", "name", "pin_weight", "alert_override"])
        else:
            emit(watch_service.suggest(ctx, top=args.top), args.json,
                 columns=["slug", "rank", "name", "median_volume", "volatility", "score"])
            print("\nNothing was added. Use wfm watch add <slug> to confirm.")
    except LookupError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0
