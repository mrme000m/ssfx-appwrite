#!/usr/bin/env python3
"""dev/scripts/setup-cf-tunnel.py — Update Cloudflare tunnel ingress.

Thin wrapper around cf_tunnel_update.py (the canonical implementation).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()


def main() -> int:
    """Delegate to the canonical cf_tunnel_update.py implementation."""
    canonical = SCRIPT_DIR / "cf_tunnel_update.py"
    return subprocess.call([sys.executable, str(canonical)])


if __name__ == "__main__":
    sys.exit(main())
