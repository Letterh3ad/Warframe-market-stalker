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


def reset() -> None:
    """Test seam: drops the slot and the task handle. Never called in production."""
    global _job, _task
    _job = None
    _task = None


def current() -> dict | None:
    return dict(_job) if _job is not None else None


async def wait() -> None:
    """Await the spawned task, if any. For tests and for a clean shutdown."""
    if _task is not None:
        await _task


async def run(ctx: AppContext, kind: str) -> dict:
    """Run one job to completion. Returns its result, or a refusal.

    A refusal is a dict with started=False rather than an exception: a manual run
    that loses the race is not an error, it is a run the tick will do anyway.
    """
    global _job
    if kind not in KINDS:
        return {"started": False, "reason": f"unknown job {kind!r}; expected one of {KINDS}"}
    if _lock.locked():
        running = _job["kind"] if _job else "a job"
        return {"started": False, "reason": f"{running} already running"}

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
                result = await news_service.classify(ctx, on_progress=_progress)
            _job["result"] = result
            return result
        except Exception as exc:  # noqa: BLE001 - the slot is the error's destination
            _job["error"] = str(exc)
            return {"error": str(exc)}
        finally:
            _job["running"] = False
            _job["finished_at"] = ctx.clock.utcnow()


def spawn(ctx: AppContext, kind: str) -> dict:
    """Start a job in the background and answer immediately.

    Ingest is ~30s and classify is minutes; neither belongs inside a request.
    """
    global _task
    if kind not in KINDS:
        return {"started": False, "reason": f"unknown job {kind!r}; expected one of {KINDS}"}
    if _lock.locked():
        running = _job["kind"] if _job else "a job"
        return {"started": False, "reason": f"{running} already running"}
    _task = asyncio.create_task(run(ctx, kind))
    return {"started": True, "kind": kind}


def _progress(external_id: str, done: int) -> None:
    if _job is not None:
        _job["done"] = done
