from __future__ import annotations

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import daemon_service


def register(parser) -> None:
    sub = parser.add_subparsers(dest="daemon_command", required=True)
    start = sub.add_parser("start")
    start.add_argument(
        "--force",
        action="store_true",
        help="clear an orphaned pid file before the single-instance guard",
    )
    for name in ("stop", "status"):
        sub.add_parser(name)
    parser.set_defaults(handler=run, force=False)


async def run(args) -> int:
    ctx = context_factory.build(args)
    try:
        if args.daemon_command == "start":
            emit(await daemon_service.start(ctx, force=args.force), args.json)
        elif args.daemon_command == "stop":
            emit(daemon_service.stop(ctx), args.json)
        else:
            emit(daemon_service.status(ctx), args.json)
    finally:
        await ctx.aclose()
    return 0
