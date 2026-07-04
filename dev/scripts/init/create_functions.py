#!/usr/bin/env python3
"""Create Appwrite Functions from appwrite/functions.json if they don't exist.

Usage: ./dev.sh create-functions
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

JSON_PATH = Path(__file__).parent.parent.parent.parent / "appwrite" / "functions.json"


def cli(*args: str) -> subprocess.CompletedProcess:
    cmd = ["appwrite", *args]
    print(f"[create-functions] {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def main() -> int:
    # Login first
    endpoint = os.environ.get("APPWRITE_ENDPOINT", "https://sgp.cloud.appwrite.io/v1")
    project = os.environ.get("APPWRITE_PROJECT_ID", "")
    key = os.environ.get("APPWRITE_API_KEY", "")
    if not project or not key:
        print("ERROR: APPWRITE_PROJECT_ID and APPWRITE_API_KEY required.", file=sys.stderr)
        return 1

    subprocess.run(
        ["appwrite", "client", "--endpoint", endpoint, "--project-id", project, "--key", key],
        check=True, capture_output=True,
    )

    with open(JSON_PATH) as f:
        functions = json.load(f)

    for fn in functions:
        fn_id = fn["$id"]
        name = fn.get("name", fn_id)
        runtime = fn.get("runtime", "node-22")
        entrypoint = fn.get("entrypoint", "src/main.js")
        execute_roles = fn.get("execute", ["any"])
        timeout = str(fn.get("timeout", 15))

        # Check if function exists
        check = cli("functions", "get", "--function-id", fn_id, "--json")
        if check.returncode == 0:
            print(f"[skip] Function {fn_id} already exists.")
            continue

        print(f"[create] Creating function {fn_id} ({name})...")
        cmd = [
            "functions", "create",
            "--function-id", fn_id,
            "--name", name,
            "--runtime", runtime,
            "--entrypoint", entrypoint,
            "--timeout", timeout,
            "--enabled", "true",
        ]
        for role in execute_roles:
            cmd.extend(["--execute", role])
        result = cli(*cmd)
        if result.returncode != 0:
            print(f"  [warn] Create error: {result.stderr}", file=sys.stderr)
        else:
            print(f"  [create] Created {fn_id}")

    print("[create-functions] Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
