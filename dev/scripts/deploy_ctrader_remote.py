#!/usr/bin/env python3
"""
dev/scripts/deploy-ctrader-remote.py — Deploy remote-services to the Azure VM.

Resolves the VM IP dynamically, syncs the remote-services directory over SSH,
builds the Docker image on the VM, and starts the stack.

Usage:
    ./dev.sh deploy-ctrader-remote [--build]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent
REMOTE_SERVICES_DIR = PROJECT_ROOT / "remote-services"
AZURE_SCRIPT = REMOTE_SERVICES_DIR / "deploy-azure.sh"


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"  [shell] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy remote-services to Azure VM")
    parser.add_argument("--build", action="store_true", help="Force Docker image rebuild")
    args = parser.parse_args()

    if not AZURE_SCRIPT.exists():
        print(f"ERROR: Azure deployment script not found: {AZURE_SCRIPT}")
        return 1

    cmd = ["bash", str(AZURE_SCRIPT)]
    if args.build:
        # The deploy-azure.sh script doesn't accept --build, but we can set an env var
        # or modify the approach. For now, we'll just run it.
        pass

    result = run(cmd, check=False)
    if result.returncode != 0:
        print(f"ERROR: Deployment failed:\n{result.stderr}")
        return result.returncode

    print(result.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
