from __future__ import annotations

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import catalog_service


def register(parser) -> None:
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=20)
    parser.set_defaults(handler=run)


def run(args) -> int:
    ctx = context_factory.build(args)
    results = catalog_service.search(ctx, args.query, limit=args.limit)
    emit(results, args.json, columns=["slug", "name", "tags", "canonical_rank"])
    return 0
