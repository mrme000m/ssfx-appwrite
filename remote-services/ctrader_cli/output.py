"""Output formatting — table and JSON renderers."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


def _fmt_ts(ts_ms: int | None) -> str:
    if not ts_ms:
        return "-"
    try:
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts_ms)


def _fmt_val(val: Any) -> str:
    if val is None:
        return "-"
    if isinstance(val, float):
        if abs(val) > 1e8:
            return f"{val:.0f}"
        return f"{val:.5f}".rstrip("0").rstrip(".")
    if isinstance(val, bool):
        return "yes" if val else "no"
    return str(val)


def print_table(
    rows: list[dict[str, Any]],
    columns: list[str] | None = None,
    *,
    title: str | None = None,
) -> None:
    """Print a list of dicts as a formatted table."""
    if title:
        print(f"\n{title}")
        print("=" * len(title))

    if not rows:
        print("  (no data)\n")
        return

    if columns is None:
        columns = list(rows[0].keys())

    widths: dict[str, int] = {}
    for col in columns:
        widths[col] = max(len(col), max(len(_fmt_val(r.get(col))) for r in rows))

    header = "  ".join(col.ljust(widths[col]) for col in columns)
    print(f"  {header}")
    print(f"  {'-' * (len(header) - 2)}")
    for row in rows:
        print("  " + "  ".join(_fmt_val(row.get(col)).ljust(widths[col]) for col in columns))
    print()


def _without_nulls(data: Any) -> Any:
    """Recursively remove null values from JSON output payloads."""
    if isinstance(data, dict):
        return {k: _without_nulls(v) for k, v in data.items() if v is not None}
    if isinstance(data, list):
        return [_without_nulls(item) for item in data]
    return data


def print_json(data: Any) -> None:
    """Print data as JSON with proper serialization."""
    def _default(o: Any) -> Any:
        if hasattr(o, "item"):
            return o.item()
        if hasattr(o, "__float__"):
            return float(o)
        return str(o)

    print(json.dumps(_without_nulls(data), indent=2, default=_default))


def print_kv(pairs: list[tuple[str, Any]], *, title: str | None = None) -> None:
    """Print key-value pairs in aligned format."""
    if title:
        print(f"\n{title}")
        print("=" * len(title))
    width = max(len(k) for k, _ in pairs) if pairs else 0
    for key, val in pairs:
        print(f"  {key.ljust(width)}  {_fmt_val(val)}")
    print()


class Output:
    """Output formatter that respects the chosen format (table or json)."""

    def __init__(self, fmt: str = "table", quiet: bool = False):
        self.fmt = fmt
        self.quiet = quiet

    def table(
        self,
        rows: list[dict[str, Any]],
        columns: list[str] | None = None,
        *,
        title: str | None = None,
    ) -> None:
        if self.fmt == "json":
            payload: dict[str, Any] = {"rows": rows}
            if title:
                payload["title"] = title
            print_json(payload)
        else:
            print_table(rows, columns, title=title)

    def kv(self, pairs: list[tuple[str, Any]], *, title: str | None = None) -> None:
        if self.fmt == "json":
            payload = dict(pairs)
            if title:
                payload["_title"] = title
            print_json(payload)
        else:
            print_kv(pairs, title=title)

    def raw(self, data: Any) -> None:
        if self.fmt == "json":
            print_json(data)
        else:
            print(data)

    def info(self, msg: str) -> None:
        if self.fmt == "json" or self.quiet:
            return
        print(msg)

    def error(self, msg: str) -> None:
        if self.fmt == "json":
            print_json({"error": msg})
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
