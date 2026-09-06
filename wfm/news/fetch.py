"""HTTP for news upstreams, on its own budget.

Deliberately not WFMClient and deliberately not TokenBucket. That client stamps
warframe.market's platform/language/crossplay parameters on every request and unwraps
its data envelope; that bucket is clamped to warframe.market's published rate limit and
exists to keep this tool compliant with it. warframe.com, forums.warframe.com and
reddit.com are unrelated hosts, so spending that budget on them would corrupt the
accounting it exists to keep. One budget per upstream, which is the same rule that makes
the GUI share the daemon's market budget.

No response cache: none of the three hosts honours a conditional GET (measured
2026-09-06, see the design doc's reconnaissance section), so there is no 304 to win.
"""

from __future__ import annotations

import httpx

from wfm.clock import Clock

_RETRYABLE = frozenset({429, 500, 502, 503, 504})


class NewsFetchError(RuntimeError):
    """One upstream failed. Ingest catches this per source and keeps the others."""


class NewsFetcher:
    def __init__(
        self,
        clock: Clock,
        user_agent: str,
        min_interval_s: float = 1.0,
        timeout_s: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int = 3,
    ) -> None:
        self._clock = clock
        self._min_interval = min_interval_s
        self._max_attempts = max_attempts
        # Per host, so one upstream's pacing never delays another's.
        self._next_allowed: dict[str, float] = {}
        self._http = httpx.AsyncClient(
            timeout=timeout_s,
            transport=transport,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_text(self, url: str) -> str:
        host = httpx.URL(url).host
        last = ""
        for attempt in range(1, self._max_attempts + 1):
            await self._pace(host)
            try:
                response = await self._http.get(url)
            except httpx.HTTPError as exc:
                last = f"connection failed: {exc!r}"
                if attempt == self._max_attempts:
                    break
                await self._clock.sleep(self._backoff(attempt))
                continue

            if response.status_code == 403:
                # Measured: the forum and reddit HTML endpoints answer 403 to every
                # non-browser client, whatever User-Agent is sent. Retrying spends
                # requests on a host that has already refused, and the fix is never a
                # retry, it is using that host's feed instead.
                raise NewsFetchError(
                    f"403 from {url}: this host blocks non-browser clients, use its feed"
                )

            if response.status_code in _RETRYABLE:
                last = f"HTTP {response.status_code} from {url}"
                if attempt == self._max_attempts:
                    break
                await self._clock.sleep(
                    self._backoff(attempt, response.headers.get("Retry-After"))
                )
                continue

            if response.status_code >= 400:
                raise NewsFetchError(f"HTTP {response.status_code} from {url}")

            return response.text

        raise NewsFetchError(f"gave up after {self._max_attempts} attempts: {last}")

    async def _pace(self, host: str | None) -> None:
        key = host or ""
        now = self._clock.now()
        allowed = self._next_allowed.get(key)
        if allowed is not None and now < allowed:
            await self._clock.sleep(allowed - now)
        self._next_allowed[key] = self._clock.now() + self._min_interval

    def _backoff(self, attempt: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                # Only the delta-seconds form is honoured. The HTTP-date form is rare
                # here and parsing it wrong would sleep for years.
                return max(float(retry_after), self._min_interval)
            except ValueError:
                pass
        return self._min_interval * (2 ** (attempt - 1))
