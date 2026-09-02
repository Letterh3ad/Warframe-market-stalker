from __future__ import annotations

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import alert_service


def register(parser) -> None:
    parser.set_defaults(handler=run)


async def run(args) -> int:
    ctx = context_factory.build(args)
    try:
        result = await alert_service.run_digest(ctx)
    finally:
        await ctx.aclose()
    emit(result, args.json)
    return 0
