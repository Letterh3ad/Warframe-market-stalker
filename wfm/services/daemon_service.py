from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import asdict

import uvicorn

from wfm.daemon import control
from wfm.daemon.runner import Daemon
from wfm.services.analysis_service import analyze_item
from wfm.services.context import AppContext
from wfm.services.feature_service import market_context
from wfm.services.report_service import poll_book
from wfm.sync.budget import Priority

STALE_AFTER_S = 15 * 60


async def start(ctx: AppContext, force: bool = False, serve_gui: bool = False) -> dict:
    if force:
        # PID recycling (routine on Windows) can make an orphaned pid file point at
        # an unrelated live process, which the guard below then reads as "already
        # running" forever. --force is the only escape short of deleting the file.
        control.clear_pid(ctx.config.pid_file)
    existing = control.read_pid(ctx.config.pid_file)
    if existing and control.is_running(existing):
        return {"started": False, "reason": f"already running as pid {existing}"}

    # The guard above proves no live daemon owns the state, so a "stopping" flag on
    # record is stale: a daemon killed (power loss, TerminateProcess) before it
    # consumed its own stop. Left in place, run() honours it and returns immediately,
    # reporting started=True with nothing started.
    if ctx.daemon_state.stop_requested():
        ctx.daemon_state.mark_stopped(ctx.clock.utcnow(), detail="stale stop flag cleared")

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

    server = None
    server_task = None
    if serve_gui:
        from wfm.gui.app import build_app
        app = build_app(ctx)
        uvicorn_config = uvicorn.Config(
            app, host=ctx.config.gui_host, port=ctx.config.gui_port, log_level="warning"
        )
        server = uvicorn.Server(uvicorn_config)
        server_task = asyncio.create_task(server.serve())

    try:
        report = await daemon.run()
    finally:
        control.clear_pid(ctx.config.pid_file)
        if server is not None:
            server.should_exit = True
            await server_task
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
        snapshot = await poll_book(ctx, entry.slug, entry.rank, priority=Priority.INTERACTIVE)
        result = analyze_item(ctx, entry.slug, entry.rank, snapshot=snapshot, market=market)
        signals.extend(result["signals"])
    return {"polled": len(entries), "signals": signals}
