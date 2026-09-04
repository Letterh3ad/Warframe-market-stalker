from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import asdict
from datetime import datetime

from wfm.daemon import control
from wfm.daemon.runner import Daemon
from wfm.services.analysis_service import analyze_item
from wfm.services.context import AppContext
from wfm.services.feature_service import market_context
from wfm.services.report_service import poll_book

STALE_AFTER_S = 15 * 60


async def start(ctx: AppContext) -> dict:
    existing = control.read_pid(ctx.config.pid_file)
    if existing and control.is_running(existing):
        return {"started": False, "reason": f"already running as pid {existing}"}

    daemon = Daemon(ctx)
    control.write_pid(ctx.config.pid_file, os.getpid())
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        handler = getattr(signal, signal_name, None)
        if handler is None:
            continue
        try:
            loop.add_signal_handler(handler, daemon.request_stop)
        except NotImplementedError:
            # Windows has no event-loop signal handlers; falling back to
            # signal.signal keeps a real SIGTERM working there too, as a second
            # path alongside the daemon_state "stopping" flag stop() below sets.
            signal.signal(handler, lambda *_: daemon.request_stop())
    try:
        report = await daemon.run()
    finally:
        control.clear_pid(ctx.config.pid_file)
    return {"started": True, **asdict(report)}


def stop(ctx: AppContext) -> dict:
    """Requests a stop through daemon_state rather than os.kill.

    os.kill on Windows has no graceful SIGTERM: anything but CTRL_C_EVENT or
    CTRL_BREAK_EVENT calls TerminateProcess, which loses the in-flight poll and
    orphans the pid file. The daemon_state "stopping" flag works identically on
    Windows and POSIX and is what the running loop already checks each iteration.
    """
    pid = control.read_pid(ctx.config.pid_file)
    if pid is None or not control.is_running(pid):
        control.clear_pid(ctx.config.pid_file)
        return {"stopped": False, "reason": "no running daemon"}
    if not ctx.daemon_state.request_stop(when=ctx.clock.utcnow()):
        return {"stopped": False, "reason": "no running daemon"}
    return {
        "stopped": True,
        "pid": pid,
        "note": "stop requested, the daemon exits after its current poll",
    }


def status(ctx: AppContext) -> dict:
    state = ctx.daemon_state.get()
    if state is None:
        return {"status": "not running"}
    heartbeat = state["heartbeat_at"]
    age = (ctx.clock.utcnow() - heartbeat).total_seconds() if heartbeat else None
    return {
        "status": state["status"],
        "pid": state["pid"],
        "started_at": state["started_at"],
        "heartbeat_at": state["heartbeat_at"],
        "heartbeat_age_seconds": age,
        "stale": age is not None and age > STALE_AFTER_S,
        "detail": state["detail"],
        "pid_alive": control.is_running(state["pid"]) if state["pid"] else False,
    }


async def scan_once(ctx: AppContext, slug: str | None = None) -> dict:
    entries = [e for e in ctx.watchlist.all() if slug is None or e.slug == slug]
    # Built once for the whole run, same reasoning as the daemon's own poll loop:
    # a catalog-wide sampled pass is not worth rebuilding per watched item.
    market = market_context(ctx, now=ctx.clock.utcnow())
    signals: list[dict] = []
    for entry in entries:
        snapshot = await poll_book(ctx, entry.slug, entry.rank)
        result = analyze_item(ctx, entry.slug, entry.rank, snapshot=snapshot, market=market)
        signals.extend(result["signals"])
    return {"polled": len(entries), "signals": signals}
