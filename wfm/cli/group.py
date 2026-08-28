from __future__ import annotations

import sys

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import group_service


def register(parser) -> None:
    sub = parser.add_subparsers(dest="group_command", required=True)
    for name in ("new", "rm", "ls", "show"):
        p = sub.add_parser(name)
        if name != "ls":
            p.add_argument("name")
    for name in ("add", "remove"):
        p = sub.add_parser(name)
        p.add_argument("name")
        p.add_argument("query")
        p.add_argument("--rank", default=None)
    parser.set_defaults(handler=run)


def run(args) -> int:
    ctx = context_factory.build(args)
    try:
        if args.group_command == "new":
            emit(group_service.new(ctx, args.name), args.json)
        elif args.group_command == "rm":
            emit(group_service.rm(ctx, args.name), args.json)
        elif args.group_command == "ls":
            emit(group_service.ls(ctx), args.json, columns=["name", "members"])
        elif args.group_command == "show":
            emit(group_service.show(ctx, args.name), args.json)
        elif args.group_command == "add":
            emit(group_service.add(ctx, args.name, args.query, rank=args.rank), args.json)
        else:
            emit(group_service.remove(ctx, args.name, args.query, rank=args.rank), args.json)
    except (LookupError, KeyError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0
