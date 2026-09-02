from __future__ import annotations

import logging

import httpx

from wfm.alerts.base import DeliveryResult
from wfm.alerts.format import render_signal
from wfm.models import Signal

MAX_MESSAGE_CHARS = 2000

log = logging.getLogger(__name__)


class DiscordSink:
    """Optional mirror of a filtered subset of signals.

    The only module in the project that issues an HTTP write. It POSTs to the one
    configured webhook and to nothing else; the market API stays strictly read only.
    Failures are logged and never propagate, because the terminal sink already holds
    the signal.
    """

    name = "discord"

    def __init__(
        self,
        webhook_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        names: dict[str, str] | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._url = webhook_url
        self._names = names or {}
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout_s)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def deliver(self, signals: list[Signal]) -> DeliveryResult:
        if not signals:
            return DeliveryResult(sink=self.name)
        body = "\n".join(render_signal(s, name=self._names.get(s.slug)) for s in signals)
        ids = [s.id for s in signals if s.id is not None]
        error = await self._post_chunks(body)
        if error:
            return DeliveryResult(sink=self.name, failed=ids, error=error)
        return DeliveryResult(sink=self.name, delivered=ids)

    async def deliver_text(self, text: str) -> DeliveryResult:
        error = await self._post_chunks(text)
        return DeliveryResult(sink=self.name, error=error)

    async def _post_chunks(self, body: str) -> str | None:
        for chunk in _chunk(body, MAX_MESSAGE_CHARS):
            try:
                response = await self._http.post(self._url, json={"content": chunk})
            except httpx.HTTPError as exc:
                log.warning("discord delivery failed: %s", exc)
                return str(exc) or exc.__class__.__name__
            if response.status_code >= 300:
                log.warning("discord delivery failed: %s", response.status_code)
                return f"{response.status_code} from webhook"
        return None


def _chunk(body: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in body.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line[:limit]
        else:
            current = candidate[:limit]
    if current:
        chunks.append(current)
    return chunks
