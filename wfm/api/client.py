from __future__ import annotations

import json
from typing import Any

import httpx

from wfm.api.backoff import delay_for, parse_retry_after
from wfm.api.breaker import CircuitBreaker
from wfm.api.errors import ApiError
from wfm.clock import Clock
from wfm.config import Config
from wfm.store.http_cache import HttpCacheRepo
from wfm.sync.budget import Budget, Priority

MAX_ATTEMPTS = 5


class WFMClient:
    """The only object in the project that owns an HTTP transport.

    Read only by construction: there is no method here that issues anything but GET,
    which is what keeps the permanent non-goal of automated trading unreachable rather
    than merely unimplemented.
    """

    def __init__(
        self,
        config: Config,
        budget: Budget,
        breaker: CircuitBreaker,
        clock: Clock,
        cache: HttpCacheRepo | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self.budget = budget
        self._breaker = breaker
        self._clock = clock
        self._cache = cache
        # Shared by every caller: a 429 is about this client's IP, not one request, so
        # backing off per request would let a concurrent caller fire into the block.
        self._hold_until = 0.0
        self._http = httpx.AsyncClient(
            timeout=config.request_timeout_s,
            transport=transport,
            headers={
                "User-Agent": config.user_agent,
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            },
        )

    async def __aenter__(self) -> WFMClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_json(
        self,
        url: str,
        params: dict | None = None,
        priority: Priority = Priority.BACKGROUND,
        use_cache: bool = False,
    ) -> Any:
        query = {
            **(params or {}),
            "platform": self._config.platform,
            "language": self._config.language,
            "crossplay": str(self._config.crossplay).lower(),
        }
        cache_key = str(httpx.URL(url).copy_merge_params(query))
        cached = self._cache.get(cache_key) if (use_cache and self._cache) else None
        headers = {"If-None-Match": cached[0]} if cached and cached[0] else {}

        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._breaker.check()
            await self._await_hold()
            await self.budget.acquire(priority)
            try:
                response = await self._http.get(url, params=query, headers=headers)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError):
                if attempt == MAX_ATTEMPTS:
                    raise
                await self._back_off(delay_for(attempt))
                continue

            if response.status_code == 304 and cached is not None:
                self._breaker.record_success()
                return _unwrap(json.loads(cached[2]))

            if response.status_code == 429:
                self._breaker.record_429()
                # Checked before sleeping, so the trip raises out of the loop rather
                # than serving one more backoff nobody will use.
                self._breaker.check()
                retry_after = parse_retry_after(response.headers.get("Retry-After"), self._clock)
                await self._back_off(delay_for(attempt, retry_after))
                continue

            if response.status_code >= 500:
                self._breaker.record_5xx()
                self._breaker.check()
                await self._back_off(delay_for(attempt))
                continue

            if response.status_code >= 400:
                self._breaker.record_success()
                raise ApiError(f"{response.status_code} from {response.url}")

            self._breaker.record_success()
            if use_cache and self._cache is not None:
                self._cache.put(
                    cache_key,
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    body=response.text,
                    when=self._clock.utcnow(),
                )
            return _unwrap(response.json())

        raise ApiError(f"gave up after {MAX_ATTEMPTS} attempts: {url}")

    @property
    def holding_until(self) -> float:
        """Monotonic instant before which no caller may issue a request."""
        return self._hold_until

    async def _await_hold(self) -> None:
        remaining = self._hold_until - self._clock.now()
        if remaining > 0:
            await self._clock.sleep(remaining)

    async def _back_off(self, delay: float) -> None:
        self._hold_until = max(self._hold_until, self._clock.now() + delay)
        await self._clock.sleep(delay)


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        error = payload.get("error")
        if error not in (None, {}, [], ""):
            raise ApiError(f"api error: {error}")
        return payload["data"]
    return payload
