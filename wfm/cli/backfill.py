from __future__ import annotations

import sys

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import catalog_service, sync_service


def register(parser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="sweep the whole catalog")
    group.add_argument("--slug", help="backfill one item")
    parser.add_argument("--limit", type=int, default=None, help="stop after N items")
    parser.add_argument("--dry-run", action="store_true")
    parser.set_defaults(handler=run)


async def run(args) -> int:
    ctx = context_factory.build(args)
    slug = None
    if args.slug:
        try:
            slug, _ = catalog_service.resolve(ctx, args.slug)
        except LookupError as exc:
            print(exc, file=sys.stderr)
            return 1
    emit(
        await sync_service.backfill(ctx, slug=slug, limit=args.limit, dry_run=args.dry_run),
        args.json,
    )
    return 0
