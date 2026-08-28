from __future__ import annotations

import json
from typing import Any


def table(rows: list[dict], columns: list[str]) -> str:
    widths = {
        c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) if rows else len(c)
        for c in columns
    }
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    body = [
        "  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns) for row in rows
    ]
    return "\n".join([header, *body])


def emit(data: Any, as_json: bool, columns: list[str] | None = None) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return
    if isinstance(data, list) and data and isinstance(data[0], dict):
        print(table(data, columns or list(data[0].keys())))
        return
    print(data)
