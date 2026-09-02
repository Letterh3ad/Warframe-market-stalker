from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable

from wfm.cli import (
    backfill,
    digest,
    group,
    holdings,
    pnl,
    report,
    search,
    signals,
    sync,
    trade,
    validate,
    watch,
)

SUBCOMMANDS: list[tuple[str, Callable]] = [
    ("sync", sync.register),
    ("backfill", backfill.register),
    ("search", search.register),
    ("report", report.register),
    ("watch", watch.register),
    ("group", group.register),
    ("validate", validate.register),
    ("signals", signals.register),
    ("digest", digest.register),
    ("trade", trade.register),
    ("holdings", holdings.register),
    ("pnl", pnl.register),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wfm", description="Warframe Market Stalker")
    parser.add_argument("--json", action="store_true", help="machine readable output")
    parser.add_argument("--verbose", action="store_true", help="request level logging")
    parser.add_argument("--config", default=None, help="path to wfm.toml")
    subparsers = parser.add_subparsers(dest="command")
    for name, register in SUBCOMMANDS:
        register(subparsers.add_parser(name))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not args.command:
        parser.print_usage()
        return 2
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    handler = args.handler
    result = handler(args)
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    return int(result or 0)
