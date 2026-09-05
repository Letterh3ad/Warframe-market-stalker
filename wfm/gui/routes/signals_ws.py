from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/signals")
async def signals_ws(websocket: WebSocket) -> None:
    ctx = websocket.app.state.ctx
    await websocket.accept()
    queue = ctx.broadcaster.subscribe()
    try:
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
        ctx.broadcaster.unsubscribe(queue)
