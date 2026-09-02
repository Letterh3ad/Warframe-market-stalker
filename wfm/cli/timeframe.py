from __future__ import annotations

from datetime import datetime, timedelta, timezone


def parse_since(value: str | None) -> datetime | None:
    """An ISO date/datetime, or a `<n>d` duration. Always returns a UTC datetime;
    an offset-aware input is converted, not relabelled. Raises ValueError on anything
    else so the caller can turn it into a clean CLI error."""
    if value is None:
        return None
    if value.endswith("d") and value[:-1].isdigit():
        return datetime.now(timezone.utc) - timedelta(days=int(value[:-1]))
    parsed = datetime.fromisoformat(value)
    return (
        parsed.astimezone(timezone.utc)
        if parsed.tzinfo is not None
        else parsed.replace(tzinfo=timezone.utc)
    )
