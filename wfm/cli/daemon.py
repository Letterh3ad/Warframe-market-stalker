from __future__ import annotations

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import daemon_service


def register(parser) -> None:
    sub = parser.add_subparsers(dest="daemon_command", required=True)
    for name in ("start", "stop", "status"):
        sub.add_parser(name)
    parser.set_defaults(handler=run)


async def run(args) -> int:
    ctx = context_factory.build(args)
    try:
        if args.daemon_command == "start":
            emit(await daemon_service.start(ctx), args.json)
        elif args.daemon_command == "stop":
            emit(daemon_service.stop(ctx), args.json)
        else:
            emit(daemon_service.status(ctx), args.json)
    finally:
        await ctx.aclose()
    return 0
