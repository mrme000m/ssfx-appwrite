#!/usr/bin/env python3
"""Sync the remote-services package to the Azure VM, then build/restart the Docker stack.

Usage:
    ./dev.sh remote-services-sync

The remote path is hard-coded to ~/ctrader-services on the VM.
Since the unified layout includes all Python packages inside remote-services/,
only this directory needs to be synced.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from _azure_vm import get_public_ip  # noqa: E402


REMOTE_USER = os.getenv("AZURE_VM_USER", "m")
REMOTE_PATH = "~/ssfx-remote-services"
RSYNC_EXCLUDES = [
    ".git",
    ".venv",
    "__pycache__",
    "*.pyc",
    ".ruff_cache",
    ".pytest_cache",
    "node_modules",
    ".logs",
    ".run",
    "*.db",
    "logs",
]


def run(cmd: list[str]) -> None:
    print(f"[sync] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def main() -> int:
    remote_ip = get_public_ip()
    print(f"[sync] Target VM: {remote_ip}")

    # Sync the self-contained remote-services directory.
    remote_services_dir = REPO_ROOT / "remote-services"
    args = ["rsync", "-avz", "--delete"]
    for pattern in RSYNC_EXCLUDES:
        args.extend(["--exclude", pattern])
    args.append(f"{remote_services_dir}/")
    args.append(f"{REMOTE_USER}@{remote_ip}:{REMOTE_PATH}/")
    run(args)

    # Build/restart the stack on the remote host.
    ssh_cmd = (
        f"cd {REMOTE_PATH} && "
        "docker compose up -d --build && "
        "docker compose restart"
    )
    run(["ssh", f"{REMOTE_USER}@{remote_ip}", ssh_cmd])

    print("[sync] Remote stack updated and restarted.")
    print(f"[sync] View logs: ssh {REMOTE_USER}@{remote_ip} 'cd {REMOTE_PATH} && docker compose logs -f'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
