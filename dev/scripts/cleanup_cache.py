#!/usr/bin/env python3
"""Remove Python cache directories and virtual environments recursively.

Removes: .pytest_cache, .ruff_cache, __pycache__, .mypy_cache, .venv
Keeps actual source files only. Safe to run repeatedly (idempotent).
"""

import os
import shutil
import sys
from pathlib import Path


def find_and_remove_caches(root_dir: Path) -> dict[str, int]:
    """Find and remove cache directories. Returns counts by type."""
    cache_patterns = [
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".venv",
        "__pycache__",
    ]

    # Also match .venv but NOT inside .venv (to avoid removing site-packages)
    removed = {p: 0 for p in cache_patterns}
    removed["total"] = 0
    removed["skipped_venv_site_packages"] = 0

    for pattern in cache_patterns:
        for cache_dir in root_dir.rglob(pattern):
            # Skip site-packages inside .venv
            if pattern == ".venv" and "site-packages" in str(cache_dir):
                removed["skipped_venv_site_packages"] += 1
                continue
            if cache_dir.is_dir():
                print(f"Removing: {cache_dir}")
                shutil.rmtree(cache_dir)
                removed[pattern] += 1
                removed["total"] += 1

    return removed


def main() -> int:
    """Main entry point."""
    root_dir = Path(__file__).parent.parent.parent
    print(f"Scanning {root_dir} for cache directories...\n")

    removed = find_and_remove_caches(root_dir)

    print(f"\nRemoved {removed['total']} cache directories:")
    for pattern, count in removed.items():
        if pattern not in ("total", "skipped_venv_site_packages"):
            print(f"  {pattern}: {count}")
    if removed["skipped_venv_site_packages"]:
        print(f"  (skipped {removed['skipped_venv_site_packages']} site-packages inside .venv)")

    return 0


if __name__ == "__main__":
    sys.exit(main())