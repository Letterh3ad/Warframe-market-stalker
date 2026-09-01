from __future__ import annotations

from typing import Any

from wfm.sync.budget import Priority

_MISSING = object()


def _longest_match(mapping: dict, url: str) -> Any:
    best_key = None
    for key in mapping:
        if key in url and (best_key is None or len(key) > len(best_key)):
            best_key = key
    return mapping[best_key] if best_key is not None else _MISSING


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
        error = _longest_match(self._errors, url)
        if error is not _MISSING:
            raise error
        return self.payload_for(url)

    def payload_for(self, url: str) -> Any:
        # Longest key wins: the statistics URL contains "/items" too, so a first-match
        # scan would hand a stats request the items payload.
        payload = _longest_match(self._payloads, url)
        if payload is _MISSING:
            raise AssertionError(f"StubClient has no payload for {url}")
        return payload

    async def aclose(self) -> None:
        return None
