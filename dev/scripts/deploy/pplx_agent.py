#!/usr/bin/env python3
"""dev.sh dispatcher for the PPLX Agent CLI.

Usage:
    ./dev.sh pplx-agent setup
    ./dev.sh pplx-agent update
    ./dev.sh pplx-agent query "..."
    ./dev.sh pplx-agent server --host 0.0.0.0 --port 9004
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PPLX_DIR = ROOT / "pplx-agent"


def main(argv: list[str]) -> int:
    if not PPLX_DIR.is_dir():
        print(f"PPLX Agent directory not found: {PPLX_DIR}", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env["PYTHONPATH"] = (
        f"{PPLX_DIR}{os.pathsep}" + env.get("PYTHONPATH", "")
    ).rstrip(os.pathsep)

    cmd = [sys.executable, "-m", "pplx_agent", *argv]
    return subprocess.run(cmd, cwd=str(PPLX_DIR), env=env).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
