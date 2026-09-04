from __future__ import annotations

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import daemon_service


def register(parser) -> None:
    parser.add_argument("--slug", default=None)
    parser.set_defaults(handler=run)


async def run(args) -> int:
    ctx = context_factory.build(args)
    try:
        emit(await daemon_service.scan_once(ctx, slug=args.slug), args.json)
    finally:
        await ctx.aclose()
    return 0
