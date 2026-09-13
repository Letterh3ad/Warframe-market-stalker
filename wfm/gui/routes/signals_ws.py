from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/signals")
async def signals_ws(websocket: WebSocket) -> None:
    ctx = websocket.app.state.ctx
    await websocket.accept()
    queue = ctx.broadcaster.subscribe()
    # The feed is send-only, but a handler that only ever awaits the queue never
    # reads the `websocket.disconnect` message, so it outlives the client and
    # uvicorn's graceful shutdown waits on the connection forever -- which hangs
    # `wfm daemon stop`. Racing a receive against the queue is what ends it.
    receiver = asyncio.ensure_future(websocket.receive())
    getter = asyncio.ensure_future(queue.get())
    try:
        while True:
            done, _ = await asyncio.wait(
                {getter, receiver}, return_when=asyncio.FIRST_COMPLETED
            )
            if receiver in done:
                if receiver.result()["type"] == "websocket.disconnect":
                    return
                # Anything else the client says is not our business, but the socket
                # has to stay read or the next disconnect goes unnoticed again.
                receiver = asyncio.ensure_future(websocket.receive())
            if getter in done:
                await websocket.send_json(getter.result())
                getter = asyncio.ensure_future(queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        for task in (getter, receiver):
            task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task
        ctx.broadcaster.unsubscribe(queue)
