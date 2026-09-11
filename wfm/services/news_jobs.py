"""One news job at a time, whoever asks for it.

The daemon tick and the GUI's two buttons run the same ingest and classify work.
Both read `pending()` before either marks anything, so two overlapping classify runs
would write every event twice. One lock, shared by both entry points, is the whole
point of this module; the job slot exists so `/news/status` can say what is running.

The lock is per-process. A `wfm news classify` in another terminal is outside it --
see DECISIONS.md 2026-09-11, recorded as a known hole rather than fixed, because
closing it means claiming rows in the database.
"""

from __future__ import annotations

import asyncio

from wfm.news.types import ArticleStatus
from wfm.services import news_service
from wfm.services.context import AppContext

KINDS = ("ingest", "classify")

_lock = asyncio.Lock()
_job: dict | None = None
_task: asyncio.Task | None = None
_reserved: str | None = None


def reset() -> None:
    """Test seam: drops the slot, the task handle, and the reservation."""
    global _job, _task, _reserved
    _job = None
    _task = None
    _reserved = None


def current() -> dict | None:
    return dict(_job) if _job is not None else None


async def wait() -> None:
    """Await the spawned task, if any. For tests and for a clean shutdown."""
    if _task is not None:
        await _task


def _reserve(kind: str) -> dict | None:
    """Claim the slot, or hand back the refusal. No await in this function, so two
    calls made back to back (spawn(); spawn();) cannot both see the slot free --
    the reservation is what makes spawn()'s answer true, not the lock, which is
    only acquired once the reserved task actually starts running its body."""
    global _reserved
    if kind not in KINDS:
        return {"started": False, "reason": f"unknown job {kind!r}; expected one of {KINDS}"}
    if _reserved is not None:
        return {"started": False, "reason": f"{_reserved} already running"}
    _reserved = kind
    return None


async def _run_reserved(ctx: AppContext, kind: str, on_progress=None) -> dict:
    """The reserved job's body. Always clears the reservation on the way out,
    including when the exception comes from outside the news_service call (e.g.
    count_articles), so an outage can never wedge the slot past this run."""
    global _job, _reserved
    try:
        async with _lock:
            total = ctx.news.count_articles(ArticleStatus.PENDING) if kind == "classify" else 0
            _job = {
                "kind": kind,
                "running": True,
                "done": 0,
                "total": total,
                "started_at": ctx.clock.utcnow(),
                "finished_at": None,
                "result": None,
                "error": None,
            }
            try:
                if kind == "ingest":
                    result = await news_service.ingest(ctx)
                else:
                    result = await news_service.classify(
                        ctx, on_progress=_chain_progress(on_progress)
                    )
                _job["result"] = result
                return result
            except Exception as exc:  # noqa: BLE001 - the slot is the error's destination
                _job["error"] = str(exc)
                return {"error": str(exc)}
            finally:
                _job["running"] = False
                _job["finished_at"] = ctx.clock.utcnow()
    finally:
        _reserved = None


async def run(ctx: AppContext, kind: str, on_progress=None) -> dict:
    """Run one job to completion. Returns its result, or a refusal.

    A refusal is a dict with started=False rather than an exception: a manual run
    that loses the race is not an error, it is a run the tick will do anyway.

    `on_progress`, if given, fires alongside the job slot's own `done` counter --
    it does not replace it. The daemon tick uses this to keep heartbeating and to
    hear a stop request during a long classify.
    """
    refusal = _reserve(kind)
    if refusal is not None:
        return refusal
    return await _run_reserved(ctx, kind, on_progress)


def spawn(ctx: AppContext, kind: str) -> dict:
    """Start a job in the background and answer immediately.

    Ingest is ~30s and classify is minutes; neither belongs inside a request.
    """
    global _task
    refusal = _reserve(kind)
    if refusal is not None:
        return refusal
    _task = asyncio.create_task(_run_reserved(ctx, kind))
    return {"started": True, "kind": kind}


def _progress(external_id: str, done: int) -> None:
    if _job is not None:
        _job["done"] = done


def _chain_progress(on_progress):
    """The job slot's own counter always updates; a caller's callback, if any,
    fires alongside it rather than instead of it."""
    if on_progress is None:
        return _progress

    def _both(external_id: str, done: int) -> None:
        _progress(external_id, done)
        on_progress(external_id, done)

    return _both
