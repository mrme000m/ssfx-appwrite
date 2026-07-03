#!/usr/bin/env python3
"""
dev/scripts/setup-cf-tunnel.py — Update Cloudflare tunnel ingress for remote-services.

Ensures all public hostnames are mapped to the correct local services on the Azure VM.

Usage:
    ./dev.sh setup-cf-tunnel
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent
TUNNEL_SCRIPT = PROJECT_ROOT / "remote-services" / "setup-cf-tunnel.sh"


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"  [shell] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def main() -> int:
    if not TUNNEL_SCRIPT.exists():
        print(f"ERROR: Tunnel setup script not found: {TUNNEL_SCRIPT}")
        return 1

    result = run(["bash", str(TUNNEL_SCRIPT)], check=False)
    if result.returncode != 0:
        print(f"ERROR: Tunnel setup failed:\n{result.stderr}")
        return result.returncode

    print(result.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
