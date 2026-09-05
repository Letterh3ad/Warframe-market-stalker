from __future__ import annotations

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.gui.app import build_app
from wfm.services import daemon_service


def register(parser) -> None:
    sub = parser.add_subparsers(dest="daemon_command", required=True)
    start = sub.add_parser("start")
    start.add_argument(
        "--force",
        action="store_true",
        help="clear an orphaned pid file before the single-instance guard",
    )
    start.add_argument(
        "--no-gui",
        action="store_false",
        dest="serve_gui",
        help="do not start the embedded web GUI alongside the daemon",
    )
    for name in ("stop", "status"):
        sub.add_parser(name)
    parser.set_defaults(handler=run, force=False, serve_gui=True)


async def run(args) -> int:
    ctx = context_factory.build(args)
    try:
        if args.daemon_command == "start":
            app = build_app(ctx) if args.serve_gui else None
            emit(
                await daemon_service.start(
                    ctx, force=args.force, serve_gui=args.serve_gui, app=app
                ),
                args.json,
            )
        elif args.daemon_command == "stop":
            emit(daemon_service.stop(ctx), args.json)
        else:
            emit(daemon_service.status(ctx), args.json)
    finally:
        await ctx.aclose()
    return 0
