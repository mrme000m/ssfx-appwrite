#!/usr/bin/env python3
"""Run remote-services/init-tunnel.py on the Azure VM.

Usage:
    ./dev.sh remote-services-init-tunnel
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from _azure_vm import get_public_ip  # noqa: E402


REMOTE_USER = os.getenv("AZURE_VM_USER", "m")
REMOTE_PATH = "~/ssfx-remote-services"


def main() -> int:
    remote_ip = get_public_ip()
    print(f"[init-tunnel] Target VM: {remote_ip}")

    cmd = (
        f"cd {REMOTE_PATH}/remote-services && "
        "python3 init-tunnel.py"
    )
    subprocess.run(["ssh", f"{REMOTE_USER}@{remote_ip}", cmd], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
