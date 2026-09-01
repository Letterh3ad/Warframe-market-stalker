from __future__ import annotations

import sys

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import report_service


def register(parser) -> None:
    parser.add_argument("query", nargs="?", help="item name or slug")
    parser.add_argument("--group", help="report on a saved group instead of one item")
    parser.add_argument("--rank", default=None)
    parser.add_argument("--refresh", action="store_true", help="fetch the live order book")
    parser.set_defaults(handler=run)


async def run(args) -> int:
    ctx = context_factory.build(args)
    try:
        if args.group:
            payload = await report_service.report_group(ctx, args.group, refresh=args.refresh)
        elif args.query:
            payload = await report_service.report(
                ctx, args.query, rank=args.rank, refresh=args.refresh
            )
        else:
            print("give an item or --group", file=sys.stderr)
            return 2
        if args.json:
            emit(payload, True)
        else:
            _print_human(payload)
        return 0
    except (LookupError, KeyError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        await ctx.aclose()


def _print_human(payload: dict) -> None:
    if "items" in payload:
        for entry in payload["items"]:
            _print_human(entry)
            print()
        return
    price = payload.get("price") or {}
    book = payload.get("book") or {}
    print(f"{payload.get('name', payload['slug'])}  (rank {payload['rank']})")
    print(f"  last close      {price.get('last_close')}")
    print(f"  median 7/30/90  {price.get('median_7d')} / {price.get('median_30d')} / "
          f"{price.get('median_90d')}")
    print(f"  robust z        {price.get('robust_z')}")
    print(f"  percentile 90d  {price.get('percentile_90d')}")
    print(f"  volume trend    {price.get('volume_trend')}")
    if book:
        print(f"  online bid/ask  {book.get('online_best_bid')} / {book.get('online_best_ask')}")
        print(f"  spread          {book.get('online_spread')} ({book.get('online_spread_pct')})")
        print(f"  stale share     {book.get('stale_share')}")
    missing = payload["provenance"]["missing"]
    if missing:
        print(f"  unavailable     {', '.join(missing)}")
