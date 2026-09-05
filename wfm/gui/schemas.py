from __future__ import annotations

from pydantic import BaseModel


class WatchlistAddRequest(BaseModel):
    query: str
    rank: str | int | None = None
    pin: float = 0.0
    alert: bool = False


class GroupCreateRequest(BaseModel):
    name: str


class GroupMemberRequest(BaseModel):
    query: str
    rank: str | int | None = None
