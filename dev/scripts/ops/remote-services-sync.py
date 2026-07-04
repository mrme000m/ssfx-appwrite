#!/usr/bin/env python3
"""Sync the remote-services package to the VM, then build/restart the Docker stack.

Usage:
    ./dev.sh remote-services-sync

Reads the SSH target from remote-services/config/vm.env (SSH_HOST variable).
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

SERVICES_DIR = REPO_ROOT / "remote-services"
VM_ENV_PATH = SERVICES_DIR / "config" / "vm.env"

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


def load_env_file(path: Path) -> None:
    """Load a dotenv-style file into os.environ (only if key is unset)."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val and os.environ.get(key) is None:
                os.environ[key] = val


def run(cmd: list[str]) -> None:
    print(f"[sync] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def main() -> int:
    load_env_file(REPO_ROOT / ".env")
    load_env_file(VM_ENV_PATH)

    ssh_host = os.environ.get("SSH_HOST", "").strip()
    if not ssh_host:
        print(
            "ERROR: SSH_HOST is not set. "
            f"Define it in {VM_ENV_PATH} or export it.",
            file=sys.stderr,
        )
        sys.exit(1)

    vm_user = os.environ.get("VM_USER", "").strip()
    remote_target = f"{vm_user}@{ssh_host}" if vm_user else ssh_host

    remote_path = os.environ.get("REMOTE_PATH", "~/ctrader-services")

    print(f"[sync] Target VM: {remote_target}")
    print(f"[sync] Remote path: {remote_path}")

    # Sync the self-contained remote-services directory.
    args = ["rsync", "-avz", "--delete"]
    for pattern in RSYNC_EXCLUDES:
        args.extend(["--exclude", pattern])
    args.append(f"{SERVICES_DIR}/")
    args.append(f"{remote_target}:{remote_path}/")
    run(args)

    # Build/restart the stack on the remote host.
    ssh_cmd = (
        f"cd {remote_path} && "
        "docker compose up -d --build && "
        "docker compose restart"
    )
    run(["ssh", remote_target, ssh_cmd])

    print("[sync] Remote stack updated and restarted.")
    print(f"[sync] View logs: ssh {remote_target} 'cd {remote_path} && docker compose logs -f'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
