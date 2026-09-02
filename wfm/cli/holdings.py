from __future__ import annotations

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import ledger_service


def register(parser) -> None:
    parser.set_defaults(handler=run)


def run(args) -> int:
    ctx = context_factory.build(args)
    emit(
        ledger_service.holdings(ctx),
        args.json,
        columns=["slug", "rank", "name", "quantity", "avg_cost", "mark", "unrealized_profit"],
    )
    return 0
