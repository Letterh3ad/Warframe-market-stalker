from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any

from wfm.api.endpoints import fetch_items, fetch_versions
from wfm.clock import Clock
from wfm.store.db import transaction
from wfm.store.items import ItemsRepo
from wfm.store.sweep import SweepStateRepo

SWEEP_NAME = "catalog"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatalogSyncResult:
    changed: bool
    version: str | None
    item_count: int
    requests_spent: int


def _version_token(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    collections = payload.get("collections")
    if isinstance(collections, dict):
        token = collections.get("items")
        if token is not None:
            return str(token)
    return str(payload.get("items")) if payload.get("items") is not None else None


async def sync_catalog(
    client,
    items_repo: ItemsRepo,
    sweep_state_repo: SweepStateRepo,
    clock: Clock,
    force: bool = False,
) -> CatalogSyncResult:
    now = clock.utcnow()
    sweep_state_repo.start(SWEEP_NAME, now)
    stored = sweep_state_repo.get(SWEEP_NAME) or {}
    previous_version = stored.get("cursor")

    version = _version_token(await fetch_versions(client))
    if version is None:
        log.warning(
            "catalog /versions returned no usable version token; refetching the full catalog"
        )
    if not force and version is not None and previous_version == version:
        sweep_state_repo.finish(SWEEP_NAME, now)
        return CatalogSyncResult(False, version, items_repo.count(), requests_spent=1)

    items = [replace(item, last_seen_version=version) for item in await fetch_items(client)]
    log.info(
        "catalog changed (%s -> %s): %d items", previous_version, version, len(items)
    )
    # One transaction across two repositories: a crash between the item write and the
    # cursor move would otherwise leave the gate pointing at a version the rows no
    # longer match. transaction() is reentrant, so the repos' own writes nest as
    # savepoints. Both repos share this connection.
    conn = sweep_state_repo._conn
    with transaction(conn):
        items_repo.upsert_many(items)
        sweep_state_repo.checkpoint(
            SWEEP_NAME, cursor=version or "", when=now, done_count=len(items)
        )
        sweep_state_repo.finish(SWEEP_NAME, now)
    return CatalogSyncResult(True, version, len(items), requests_spent=2)
