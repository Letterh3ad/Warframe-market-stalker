from __future__ import annotations

import sys

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import news_service


def register(parser) -> None:
    modes = parser.add_subparsers(dest="news_command", required=True)
    ingest = modes.add_parser("ingest", help="fetch each configured source once")
    ingest.add_argument(
        "--force", action="store_true", help="run even when news_enabled is false"
    )
    classify = modes.add_parser("classify", help="classify the pending articles")
    classify.add_argument(
        "--limit", type=int, default=None, help="articles this run (default: config)"
    )
    modes.add_parser(
        "relink", help="re-resolve predicted Prime slugs against the current catalog"
    )
    modes.add_parser("status", help="corpus size, pending count and backend")
    parser.set_defaults(handler=run)


async def run(args) -> int:
    ctx = context_factory.build(args)
    try:
        if args.news_command == "status":
            emit(news_service.status(ctx), args.json)
        elif args.news_command == "classify":
            # Both raising paths out of build_classifier: ValueError for a name
            # outside KNOWN_CLASSIFIERS, ClassifierError when the configured backend
            # needs an optional extra that is not installed. Both messages already
            # say what to do, so print and stop rather than traceback.
            try:
                summary = await news_service.classify(ctx, limit=args.limit)
            except (ValueError, news_service.ClassifierError) as exc:
                print(exc, file=sys.stderr)
                return 1
            emit(summary, args.json)
            # A failed article is terminal until it is re-ingested with changed
            # content, so exiting 0 on a run that classified nothing is how a user
            # finds out weeks later.
            return 1 if summary["failed"] else 0
        elif args.news_command == "relink":
            emit(news_service.reconcile_synthetic_links(ctx), args.json)
        else:
            emit(await news_service.ingest(ctx, force=args.force), args.json)
        return 0
    finally:
        await ctx.aclose()
