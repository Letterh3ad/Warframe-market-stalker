from __future__ import annotations

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import news_service


def register(parser) -> None:
    modes = parser.add_subparsers(dest="news_command", required=True)
    ingest = modes.add_parser("ingest", help="fetch each configured source once")
    ingest.add_argument(
        "--force", action="store_true", help="run even when news_enabled is false"
    )
    modes.add_parser("status", help="corpus size and pending count")
    parser.set_defaults(handler=run)


async def run(args) -> int:
    ctx = context_factory.build(args)
    try:
        if args.news_command == "status":
            emit(news_service.status(ctx), args.json)
            return 0
        emit(await news_service.ingest(ctx, force=args.force), args.json)
        return 0
    finally:
        await ctx.aclose()
