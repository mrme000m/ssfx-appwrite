#!/usr/bin/env python3
"""Show status of local services and the remote AWS VM."""

import os
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
VM_ENV = PROJECT_ROOT / "remote-services" / "config" / "vm.env"


def load_vm_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if VM_ENV.exists():
        for line in VM_ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def run_ssh(host: str, command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new", host, command],
        capture_output=True,
        text=True,
    )


def main() -> int:
    print("[dev] Local service status:")
    # TODO: add status checks for local services.
    print("  (local service checks not yet implemented)")

    env = load_vm_env()
    ssh_host = env.get("SSH_HOST", "aws-ssfx")
    vm_user = env.get("VM_USER", "ec2-user")
    remote_dir = env.get("REMOTE_DIR", f"/home/{vm_user}/ssfx-remote-services")

    print(f"\n[dev] Remote VM status ({ssh_host}):")
    result = run_ssh(ssh_host, f"cd {remote_dir} && docker compose ps")
    if result.returncode == 0:
        print(result.stdout)
    else:
        print(f"  Could not reach {ssh_host}:")
        print(result.stderr or "  (no output)", file=sys.stderr)

    print("\n[dev] All status checks completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
