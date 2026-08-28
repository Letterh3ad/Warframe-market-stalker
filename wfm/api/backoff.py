from __future__ import annotations

import math
from datetime import timezone
from email.utils import parsedate_to_datetime

from wfm.clock import Clock

MAX_HONOURED_RETRY_AFTER = 3600.0
MIN_RETRY_AFTER = 1.0


def delay_for(
    attempt: int, retry_after: float | None = None, base: float = 2.0, cap: float = 300.0
) -> float:
    # Retry-After is honored as sent, local cap included, because the server's number
    # is authoritative about when it will serve us again. A value that is not a sane
    # number of seconds is not authoritative about anything, so it falls back to the
    # curve rather than producing an immediate retry or an unbounded sleep.
    if (
        retry_after is not None
        and math.isfinite(retry_after)
        and 0.0 <= retry_after <= MAX_HONOURED_RETRY_AFTER
    ):
        return max(MIN_RETRY_AFTER, retry_after)
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
    # A "-0000" zone is legal and parses to a naive datetime, which cannot be
    # subtracted from an aware now().
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - clock.utcnow()).total_seconds())
