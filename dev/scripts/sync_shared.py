#!/usr/bin/env python3
"""
dev/scripts/sync_shared.py

Copies the canonical shared module at functions/_shared/ into every
function's src/_shared/ directory. This lets us maintain one source of truth
while still packaging the shared code inside each function for Appwrite
deployments.

Usage:
    ./dev.sh sync-shared
"""

import json
import shutil
import sys
from pathlib import Path


def get_project_root() -> Path:
    return Path(__file__).parent.parent.parent.resolve()


def get_function_ids(root: Path) -> list[str]:
    """Read function IDs from appwrite/functions.json."""
    functions_json = root / "appwrite" / "functions.json"
    if not functions_json.exists():
        print(f"Error: {functions_json} not found", file=sys.stderr)
        sys.exit(1)
    with open(functions_json) as f:
        data = json.load(f)
    return [fn["$id"] for fn in data if "$id" in fn]


def sync_function_shared(root: Path, function_id: str) -> None:
    source = root / "functions" / "_shared"
    target = root / "functions" / function_id / "src" / "_shared"

    if not source.exists():
        print(f"Error: canonical shared module missing at {source}", file=sys.stderr)
        sys.exit(1)

    if target.exists():
        shutil.rmtree(target)

    shutil.copytree(source, target)
    print(f"[sync-shared] {function_id} <- functions/_shared")


def main() -> None:
    root = get_project_root()
    function_ids = get_function_ids(root)

    if not function_ids:
        print("[sync-shared] No functions found; nothing to do.")
        return

    for function_id in function_ids:
        sync_function_shared(root, function_id)

    print(f"[sync-shared] Synced shared module into {len(function_ids)} function(s).")


if __name__ == "__main__":
    main()
