from __future__ import annotations

from email.utils import parsedate_to_datetime

from wfm.clock import Clock


def delay_for(
    attempt: int, retry_after: float | None = None, base: float = 2.0, cap: float = 300.0
) -> float:
    # Retry-After is honored exactly as sent, cap included, because the server's number
    # is authoritative about when it will serve us again.
    if retry_after is not None:
        return max(0.0, retry_after)
    return min(cap, base * (2 ** (attempt - 1)))


def parse_retry_after(value: str | None, clock: Clock) -> float | None:
    if value is None:
        return None
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(0.0, (when - clock.utcnow()).total_seconds())
