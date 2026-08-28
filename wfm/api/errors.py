from __future__ import annotations


class ApiError(Exception):
    """Any non-retryable failure talking to warframe.market."""


class RateLimited(ApiError):
    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


class CircuitOpen(ApiError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
