from __future__ import annotations

import asyncio


class SignalBroadcaster:
    """Fan-out of newly-persisted signals to every subscribed listener (a GUI
    WebSocket client, one queue each). Framework-agnostic: knows nothing about FastAPI
    or WebSockets, just asyncio.Queue, so it carries no import that would trip the
    frontend-boundary architecture test if a services-layer module ever needed it.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, payload: dict) -> None:
        for queue in self._subscribers:
            queue.put_nowait(payload)
