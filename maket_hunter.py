#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import requests

BASE_URL = "https://api.warframe.market/v2"

DEFAULT_PLATFORM = "pc"
DEFAULT_LANGUAGE = "en"
DEFAULT_LOCALE = "en"
DEFAULT_CROSSPLAY = True

DB_FILE_DEFAULT = "wfm_market.db"
ITEMS_CACHE_DEFAULT = "wfm_items_cache.json"

_slug_cleanup_re = re.compile(r"[^a-z0-9_]+")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_slug(name: str) -> str:
    s = name.strip().lower().replace(" ", "_")
    s = _slug_cleanup_re.sub("", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


@dataclass(frozen=True)
class ClientConfig:
    platform: str = DEFAULT_PLATFORM
    language: str = DEFAULT_LANGUAGE
    locale: str = DEFAULT_LOCALE
    crossplay: bool = DEFAULT_CROSSPLAY


class WFMClient:
    def __init__(self, cfg: ClientConfig, timeout_s: int = 20) -> None:
        self.cfg = cfg
        self.timeout_s = timeout_s
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "wfm-market-hunter/1.3 (+https://warframe.market)",
            }
        )

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if params is None:
            params = {}
        params = {
            **params,
            "platform": self.cfg.platform,
            "language": self.cfg.language,
            "locale": self.cfg.locale,
            "crossplay": str(self.cfg.crossplay).lower(),
        }

        url = f"{BASE_URL}{path}"
        r = self.session.get(url, params=params, timeout=self.timeout_s)
        r.raise_for_status()
        j = r.json()

        if isinstance(j, dict) and "data" in j:
            if j.get("error") not in (None, {}, [], ""):
                raise RuntimeError(f"API error from {url}: {j.get('error')}")
            return j["data"]

        return j

    def list_items(self) -> List[Dict[str, Any]]:
        data = self._get("/items")

        if isinstance(data, list):
            return [it for it in data if isinstance(it, dict)]

        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                return [it for it in items if isinstance(it, dict)]

        raise RuntimeError(f"Unexpected /v2/items response shape: {type(data)}")

    def orders_top(self, slug: str) -> Any:
        return self._get(f"/orders/item/{slug}/top")


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            slug TEXT NOT NULL,

            -- Sell-side (what sellers list for)
            sell_min INTEGER,
            sell_max INTEGER,
            sell_avg REAL,
            sell_p25 REAL,
            sell_p50 REAL,
            sell_p75 REAL,
            sell_count INTEGER NOT NULL,

            -- Buy-side (what buyers offer)
            buy_min INTEGER,
            buy_max INTEGER,
            buy_avg REAL,
            buy_p25 REAL,
            buy_p50 REAL,
            buy_p75 REAL,
            buy_count INTEGER NOT NULL,

            -- Cross stats
            spread_best INTEGER,      -- sell_min - buy_max
            spread_avg REAL,          -- sell_avg - buy_avg
            undercut_pressure REAL,   -- sell_p25 - sell_min (bigger => more undercutting at bottom)
            market_depth INTEGER,     -- sell_count + buy_count

            sell_top5 TEXT NOT NULL,
            buy_top5 TEXT NOT NULL,
            raw_top_json TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_slug_ts ON snapshots(slug, ts_utc)")
    conn.commit()


def load_items_cached(client: WFMClient, cache_path: str, force: bool) -> List[Dict[str, Any]]:
    if not force and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, dict) and isinstance(cached.get("items"), list):
                return [it for it in cached["items"] if isinstance(it, dict)]
        except Exception:
            pass

    items = client.list_items()
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"fetched_at_utc": now_utc_iso(), "items": items}, f, ensure_ascii=False)
    except Exception:
        pass
    return items


def item_slug(it: Dict[str, Any]) -> Optional[str]:
    for k in ("slug", "url_name", "urlName"):
        v = it.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def item_name(it: Dict[str, Any]) -> str:
    for k in ("item_name", "name", "display_name", "displayName"):
        v = it.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    slug = item_slug(it)
    return slug or "<unknown>"


def resolve_slug(client: WFMClient, item_input: str, cache_path: str, assume_slug: bool) -> Tuple[str, List[str]]:
    if assume_slug:
        return to_slug(item_input), []

    items = load_items_cached(client, cache_path=cache_path, force=False)

    names: List[str] = []
    name_to_slug: Dict[str, str] = {}

    for it in items:
        slug = item_slug(it)
        if not slug:
            continue
        nm = item_name(it).lower()
        names.append(nm)
        name_to_slug[nm] = slug

    q = item_input.strip().lower()
    if q in name_to_slug:
        return name_to_slug[q], []

    matches = difflib.get_close_matches(q, names, n=10, cutoff=0.55)
    suggestions = matches[:10]

    if matches:
        return name_to_slug[matches[0]], suggestions

    return to_slug(item_input), suggestions


def extract_buy_sell_lists(top_payload: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    buy: List[Dict[str, Any]] = []
    sell: List[Dict[str, Any]] = []

    def push_order(o: Dict[str, Any]) -> None:
        t = o.get("order_type") or o.get("type")
        if t == "buy":
            buy.append(o)
        elif t == "sell":
            sell.append(o)

    if isinstance(top_payload, dict):
        if isinstance(top_payload.get("buy"), list) and isinstance(top_payload.get("sell"), list):
            buy = [o for o in top_payload["buy"] if isinstance(o, dict)]
            sell = [o for o in top_payload["sell"] if isinstance(o, dict)]
            return buy, sell

        if isinstance(top_payload.get("orders"), list):
            for o in top_payload["orders"]:
                if isinstance(o, dict):
                    push_order(o)
            return buy, sell

    if isinstance(top_payload, list):
        for o in top_payload:
            if isinstance(o, dict):
                push_order(o)
        return buy, sell

    return buy, sell


def order_price(o: Dict[str, Any]) -> Optional[int]:
    p = o.get("platinum") if "platinum" in o else o.get("price")
    if isinstance(p, (int, float)):
        return int(p)
    if isinstance(p, str):
        try:
            return int(float(p))
        except Exception:
            return None
    return None


def percentile(sorted_vals: List[int], q: float) -> Optional[float]:
    if not sorted_vals:
        return None
    if q <= 0:
        return float(sorted_vals[0])
    if q >= 1:
        return float(sorted_vals[-1])
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def compute_snapshot(slug: str, top_payload: Any) -> Dict[str, Any]:
    buy_orders, sell_orders = extract_buy_sell_lists(top_payload)

    buy_prices = sorted([p for p in (order_price(o) for o in buy_orders) if p is not None])
    sell_prices = sorted([p for p in (order_price(o) for o in sell_orders) if p is not None])

    sell_min = sell_prices[0] if sell_prices else None  # lowest someone is selling for (worst for you)
    sell_max = sell_prices[-1] if sell_prices else None
    sell_avg = (sum(sell_prices) / len(sell_prices)) if sell_prices else None

    buy_min = buy_prices[0] if buy_prices else None
    buy_max = buy_prices[-1] if buy_prices else None  # highest someone is buying for (best instant sell)
    buy_avg = (sum(buy_prices) / len(buy_prices)) if buy_prices else None

    sell_p25 = percentile(sell_prices, 0.25)
    sell_p50 = percentile(sell_prices, 0.50)
    sell_p75 = percentile(sell_prices, 0.75)

    buy_p25 = percentile(buy_prices, 0.25)
    buy_p50 = percentile(buy_prices, 0.50)
    buy_p75 = percentile(buy_prices, 0.75)

    spread_best = (sell_min - buy_max) if (sell_min is not None and buy_max is not None) else None
    spread_avg = (sell_avg - buy_avg) if (sell_avg is not None and buy_avg is not None) else None
    undercut_pressure = (sell_p25 - sell_min) if (sell_p25 is not None and sell_min is not None) else None
    market_depth = len(sell_prices) + len(buy_prices)

    return {
        "ts_utc": now_utc_iso(),
        "slug": slug,
        "sell_min": sell_min,
        "sell_max": sell_max,
        "sell_avg": sell_avg,
        "sell_p25": sell_p25,
        "sell_p50": sell_p50,
        "sell_p75": sell_p75,
        "sell_count": len(sell_prices),
        "buy_min": buy_min,
        "buy_max": buy_max,
        "buy_avg": buy_avg,
        "buy_p25": buy_p25,
        "buy_p50": buy_p50,
        "buy_p75": buy_p75,
        "buy_count": len(buy_prices),
        "spread_best": spread_best,
        "spread_avg": spread_avg,
        "undercut_pressure": undercut_pressure,
        "market_depth": market_depth,
        "sell_top5": json.dumps(sell_prices[:5]),
        "buy_top5": json.dumps(list(reversed(buy_prices[-5:]))),  # show highest buy offers first
        "raw_top_json": json.dumps(top_payload),
    }


def save_snapshot(conn: sqlite3.Connection, snap: Dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO snapshots (
            ts_utc, slug,
            sell_min, sell_max, sell_avg, sell_p25, sell_p50, sell_p75, sell_count,
            buy_min, buy_max, buy_avg, buy_p25, buy_p50, buy_p75, buy_count,
            spread_best, spread_avg, undercut_pressure, market_depth,
            sell_top5, buy_top5, raw_top_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snap["ts_utc"],
            snap["slug"],
            snap["sell_min"],
            snap["sell_max"],
            snap["sell_avg"],
            snap["sell_p25"],
            snap["sell_p50"],
            snap["sell_p75"],
            snap["sell_count"],
            snap["buy_min"],
            snap["buy_max"],
            snap["buy_avg"],
            snap["buy_p25"],
            snap["buy_p50"],
            snap["buy_p75"],
            snap["buy_count"],
            snap["spread_best"],
            snap["spread_avg"],
            snap["undercut_pressure"],
            snap["market_depth"],
            snap["sell_top5"],
            snap["buy_top5"],
            snap["raw_top_json"],
        ),
    )
    conn.commit()

def pretty_item_label(item_input: str, slug: str, assume_slug: bool) -> str:
    # If you passed --slug, item_input is already a slug; otherwise it’s a human name.
    if assume_slug:
        return f"{slug}"
    return f"{item_input} ({slug})"


def fetch_and_store_one(
    conn: sqlite3.Connection,
    client: WFMClient,
    item_input: str,
    cache_path: str,
    assume_slug: bool,
    quiet: bool,
) -> bool:
    slug, suggestions = resolve_slug(client, item_input, cache_path=cache_path, assume_slug=assume_slug)

    top = client.orders_top(slug)
    snap = compute_snapshot(slug, top)
    save_snapshot(conn, snap)

    if not quiet:
        label = pretty_item_label(item_input, slug, assume_slug)
        print(f"\n{label}")

        if suggestions and not assume_slug:
            for s in suggestions[:5]:
                print(f"  suggestion: {s}")

        if snap["sell_avg"] is not None:
            print(
                f"  SELL: avg={snap['sell_avg']:.2f} min(worst)={snap['sell_min']} p50={snap['sell_p50']} "
                f"p25={snap['sell_p25']} p75={snap['sell_p75']} count={snap['sell_count']} top5={snap['sell_top5']}"
            )
        else:
            print("  SELL: no data")

        if snap["buy_avg"] is not None:
            print(
                f"  BUY : max(best)={snap['buy_max']} avg={snap['buy_avg']:.2f} p50={snap['buy_p50']} "
                f"p25={snap['buy_p25']} p75={snap['buy_p75']} count={snap['buy_count']} top5={snap['buy_top5']}"
            )
        else:
            print("  BUY : no data")

        if snap["spread_avg"] is not None and snap["undercut_pressure"] is not None:
            print(
                f"  SPREAD(best)={snap['spread_best']}  SPREAD(avg)={snap['spread_avg']:.2f}  "
                f"undercut_pressure={snap['undercut_pressure']:.2f}  depth={snap['market_depth']}"
            )
        else:
            print(f"  SPREAD(best)={snap['spread_best']} depth={snap['market_depth']}")

    return True


def iter_mods_from_txt(path: str) -> List[str]:
    mods: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            mods.append(s)

    seen = set()
    out: List[str] = []
    for m in mods:
        k = m.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(m)
    return out


def fetch_and_store_file(
    conn: sqlite3.Connection,
    client: WFMClient,
    txt_path: str,
    cache_path: str,
    assume_slug: bool,
    quiet: bool,
) -> None:
    mods = iter_mods_from_txt(txt_path)
    if not mods:
        print("No mods found in txt file.")
        return

    ok = 0
    fail = 0
    for m in mods:
        try:
            fetch_and_store_one(conn, client, m, cache_path, assume_slug, quiet)
            ok += 1
        except Exception as e:
            fail += 1
            if not quiet:
                print(f"[FAIL] {m}: {e}")

    print(f"Done. success={ok} failed={fail} total={len(mods)}")


def plot_history(conn: sqlite3.Connection, slug: str, days: Optional[int]) -> None:
    params: List[Any] = [slug]
    where = "WHERE slug = ?"
    if days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
        where += " AND ts_utc >= ?"
        params.append(cutoff)

    rows = conn.execute(
        f"""
        SELECT ts_utc,
               sell_min, sell_avg, sell_p50,
               buy_max, buy_avg, buy_p50,
               spread_best, spread_avg, undercut_pressure
        FROM snapshots
        {where}
        ORDER BY ts_utc ASC
        """,
        params,
    ).fetchall()

    if not rows:
        print("No data yet. Run fetch a few times first.")
        return

    ts = [datetime.fromisoformat(r[0]) for r in rows]
    sell_min = [r[1] for r in rows]
    sell_avg = [r[2] for r in rows]
    sell_p50 = [r[3] for r in rows]
    buy_max = [r[4] for r in rows]
    buy_avg = [r[5] for r in rows]
    buy_p50 = [r[6] for r in rows]

    plt.figure()
    plt.plot(ts, sell_min, label="sell_min (lowest listing)")
    plt.plot(ts, sell_avg, label="sell_avg")
    plt.plot(ts, sell_p50, label="sell_p50 (median)")
    plt.plot(ts, buy_max, label="buy_max (best offer)")
    plt.plot(ts, buy_avg, label="buy_avg")
    plt.plot(ts, buy_p50, label="buy_p50 (median)")
    plt.xlabel("Time (UTC)")
    plt.ylabel("Platinum")
    plt.title(slug)
    plt.legend()
    plt.tight_layout()
    plt.show()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="maket_hunter.py")
    p.add_argument("--db", default=DB_FILE_DEFAULT)
    p.add_argument("--platform", default=DEFAULT_PLATFORM)
    p.add_argument("--language", default=DEFAULT_LANGUAGE)
    p.add_argument("--locale", default=DEFAULT_LOCALE)
    p.add_argument("--crossplay", action="store_true", default=DEFAULT_CROSSPLAY)
    p.add_argument("--no-crossplay", dest="crossplay", action="store_false")
    p.add_argument("--items-cache", default=ITEMS_CACHE_DEFAULT)

    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="Fetch current top orders and store snapshot for one item")
    f.add_argument("item", help='Item name (e.g. "archon continuity") or slug if --slug')
    f.add_argument("--slug", action="store_true", help="Treat input as slug and skip name lookup")

    ff = sub.add_parser("fetch-file", help="Fetch and store snapshot for each line in a txt file")
    ff.add_argument("txt", help="Path to txt file (one mod/item per line)")
    ff.add_argument("--slug", action="store_true", help="Treat each line as slug and skip name lookup")
    ff.add_argument("--quiet", action="store_true", help="Only print summary")

    r = sub.add_parser("refresh-items", help="Force refresh items cache")
    r.add_argument("--force", action="store_true", help="Force re-download")

    pl = sub.add_parser("plot", help="Plot stored history for a slug")
    pl.add_argument("slug", help="Item slug (e.g. archon_continuity)")
    pl.add_argument("--days", type=int, default=None, help="Limit to last N days")

    return p


def main() -> int:
    args = build_parser().parse_args()

    client = WFMClient(
        ClientConfig(
            platform=args.platform,
            language=args.language,
            locale=args.locale,
            crossplay=bool(args.crossplay),
        )
    )

    conn = sqlite3.connect(args.db)
    init_db(conn)

    if args.cmd == "refresh-items":
        load_items_cached(client, cache_path=args.items_cache, force=True)
        print(f"Items cache refreshed: {args.items_cache}")
        return 0

    if args.cmd == "fetch":
        fetch_and_store_one(
            conn,
            client,
            args.item,
            cache_path=args.items_cache,
            assume_slug=bool(args.slug),
            quiet=False,
        )
        return 0

    if args.cmd == "fetch-file":
        fetch_and_store_file(
            conn,
            client,
            args.txt,
            cache_path=args.items_cache,
            assume_slug=bool(args.slug),
            quiet=bool(args.quiet),
        )
        return 0

    if args.cmd == "plot":
        plot_history(conn, slug=args.slug, days=args.days)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
