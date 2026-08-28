from __future__ import annotations

from typing import Any

from wfm.sync.budget import Priority


class StubClient:
    """Stands in for WFMClient. Matches URLs by substring key."""

    def __init__(
        self, payloads: dict[str, Any], errors: dict[str, Exception] | None = None
    ) -> None:
        self._payloads = payloads
        self._errors = errors or {}
        self.calls: list[tuple[str, Priority]] = []

    async def get_json(
        self,
        url: str,
        params: dict | None = None,
        priority: Priority = Priority.BACKGROUND,
        use_cache: bool = False,
    ) -> Any:
        self.calls.append((url, priority))
        for key, error in self._errors.items():
            if key in url:
                raise error
        return self.payload_for(url)

    def payload_for(self, url: str) -> Any:
        for key, payload in self._payloads.items():
            if key in url:
                return payload
        raise AssertionError(f"StubClient has no payload for {url}")

    async def aclose(self) -> None:
        return None
