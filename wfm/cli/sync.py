from __future__ import annotations

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import sync_service


def register(parser) -> None:
    parser.add_argument("--force", action="store_true", help="ignore the version gate")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true", help="report sweep state and exit")
    parser.set_defaults(handler=run)


async def run(args) -> int:
    ctx = context_factory.build(args)
    if args.status:
        emit(sync_service.status(ctx), args.json)
        return 0
    emit(await sync_service.sync(ctx, force=args.force, dry_run=args.dry_run), args.json)
    return 0
