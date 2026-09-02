from __future__ import annotations

from datetime import datetime, timedelta, timezone

from wfm.cli import context_factory
from wfm.cli.output import emit
from wfm.services import alert_service


def register(parser) -> None:
    parser.add_argument("--since", default=None, help="ISO date, or a duration like 7d")
    parser.add_argument("--analyzer", default=None)
    parser.add_argument("--slug", default=None)
    parser.add_argument("--limit", type=int, default=50)
    parser.set_defaults(handler=run)


def _parse_since(value: str | None) -> datetime | None:
    if value is None:
        return None
    if value.endswith("d") and value[:-1].isdigit():
        return datetime.now(timezone.utc) - timedelta(days=int(value[:-1]))
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def run(args) -> int:
    ctx = context_factory.build(args)
    since = _parse_since(args.since)
    if args.json:
        emit(
            alert_service.list_signals(
                ctx, since=since, analyzer=args.analyzer, slug=args.slug, limit=args.limit
            ),
            True,
        )
        return 0
    print(
        alert_service.render_signals(
            ctx, since=since, analyzer=args.analyzer, slug=args.slug, limit=args.limit
        )
    )
    return 0
