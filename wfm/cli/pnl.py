from __future__ import annotations

from datetime import datetime, timezone

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import ledger_service


def register(parser) -> None:
    parser.add_argument("--realized", action="store_true", help="skip open positions")
    parser.add_argument("--since", default=None, help="ISO date")
    parser.set_defaults(handler=run)


def run(args) -> int:
    ctx = context_factory.build(args)
    since = (
        datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc) if args.since else None
    )
    emit(ledger_service.pnl(ctx, since=since, realized_only=args.realized), args.json)
    return 0
