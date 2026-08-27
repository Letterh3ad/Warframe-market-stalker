from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import date as date_type
from datetime import datetime, timedelta, timezone

from wfm.models import DailyCandle, HourlyCandle
from wfm.store.db import to_utc_iso, transaction

_DAILY_COLS = (
    'slug, "rank", date, volume, open, high, low, close, median, avg_price, '
    "wa_price, moving_avg, donch_top, donch_bot"
)
_HOURLY_COLS = 'slug, "rank", ts, volume, open, high, low, close, median, avg_price, wa_price'


class DailyStatsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_many(self, candles: Iterable[DailyCandle]) -> int:
        """Whole-row replace: pass complete candles, since an omitted column nulls the
        stored value. Only catalog sync and backfill call this, from a full API payload.
        """
        rows = [
            (
                c.slug, c.rank, c.date, c.volume, c.open, c.high, c.low, c.close,
                c.median, c.avg_price, c.wa_price, c.moving_avg, c.donch_top, c.donch_bot,
            )
            for c in candles
        ]
        with transaction(self._conn):
            self._conn.executemany(
                f"INSERT OR REPLACE INTO daily_stats ({_DAILY_COLS}) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        return len(rows)

    def window(
        self, slug: str, rank: int, days: int, end: str | date_type | None = None
    ) -> list[DailyCandle]:
        end_date = str(end) if end is not None else datetime.now(timezone.utc).date().isoformat()
        start_date = (date_type.fromisoformat(end_date) - timedelta(days=days - 1)).isoformat()
        rows = self._conn.execute(
            f"SELECT {_DAILY_COLS} FROM daily_stats "
            'WHERE slug=? AND "rank"=? AND date BETWEEN ? AND ? ORDER BY date',
            (slug, rank, start_date, end_date),
        )
        return [_to_daily(r) for r in rows]

    def latest_date(self, slug: str, rank: int) -> str | None:
        row = self._conn.execute(
            'SELECT MAX(date) FROM daily_stats WHERE slug=? AND "rank"=?', (slug, rank)
        ).fetchone()
        return row[0]

    def ranks_for(self, slug: str) -> list[int]:
        rows = self._conn.execute(
            'SELECT DISTINCT "rank" FROM daily_stats WHERE slug=? ORDER BY "rank"', (slug,)
        )
        return [int(r[0]) for r in rows]

    def market_dates(self, limit: int) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT date FROM daily_stats ORDER BY date DESC LIMIT ?", (limit,)
        )
        return sorted(r[0] for r in rows)


class HourlyStatsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_many(self, candles: Iterable[HourlyCandle]) -> int:
        """Whole-row replace: pass complete candles, since an omitted column nulls the
        stored value. Only catalog sync and backfill call this, from a full API payload.
        """
        rows = [
            (
                c.slug, c.rank, to_utc_iso(c.ts), c.volume, c.open, c.high, c.low,
                c.close, c.median, c.avg_price, c.wa_price,
            )
            for c in candles
        ]
        with transaction(self._conn):
            self._conn.executemany(
                f"INSERT OR REPLACE INTO hourly_stats ({_HOURLY_COLS}) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        return len(rows)

    def window(self, slug: str, rank: int, hours: int) -> list[HourlyCandle]:
        rows = self._conn.execute(
            f"SELECT {_HOURLY_COLS} FROM hourly_stats "
            'WHERE slug=? AND "rank"=? ORDER BY ts DESC LIMIT ?',
            (slug, rank, hours),
        )
        return sorted((_to_hourly(r) for r in rows), key=lambda c: c.ts)

    def prune(self, before: datetime) -> int:
        with transaction(self._conn):
            cur = self._conn.execute("DELETE FROM hourly_stats WHERE ts < ?", (to_utc_iso(before),))
        return cur.rowcount


def _to_daily(row: sqlite3.Row) -> DailyCandle:
    return DailyCandle(
        slug=row["slug"], rank=row["rank"], date=row["date"], volume=row["volume"],
        open=row["open"], high=row["high"], low=row["low"], close=row["close"],
        median=row["median"], avg_price=row["avg_price"], wa_price=row["wa_price"],
        moving_avg=row["moving_avg"], donch_top=row["donch_top"], donch_bot=row["donch_bot"],
    )


def _to_hourly(row: sqlite3.Row) -> HourlyCandle:
    return HourlyCandle(
        slug=row["slug"], rank=row["rank"], ts=datetime.fromisoformat(row["ts"]),
        volume=row["volume"], open=row["open"], high=row["high"], low=row["low"],
        close=row["close"], median=row["median"], avg_price=row["avg_price"],
        wa_price=row["wa_price"],
    )
