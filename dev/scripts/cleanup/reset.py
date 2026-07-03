#!/usr/bin/env python3
"""dev/scripts/reset.py — Reset the entire users database and optionally set up admin PIN.

This script provides a comprehensive reset functionality that:
1. Resets the entire users database (deletes all users and auth data)
2. Optionally sets up the admin PIN interactively
3. Avoids redundancy with existing init-scripts

Usage:
    ./dev.sh reset [--all] [--interactive-admin]
    
    --all: Also clear runtime/trading tables (default: auth tables only)
    --interactive-admin: Prompt for admin PIN setup after reset
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run_command(cmd: list[str], capture_output: bool = False) -> tuple[int, str | None]:
    """Run a shell command and return exit code and output."""
    try:
        if capture_output:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            return result.returncode, result.stdout
        else:
            result = subprocess.run(cmd, check=False)
            return result.returncode, None
    except Exception as e:
        print(f"Error running command: {e}", file=sys.stderr)
        return 1, None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reset the entire users database and optionally set up admin PIN."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Also clear runtime/trading tables (default: auth tables only)",
    )
    parser.add_argument(
        "--interactive-admin",
        action="store_true",
        help="Prompt for admin PIN setup after reset",
    )
    args = parser.parse_args()

    # Build the reset-admin command
    reset_cmd = ["./dev.sh", "reset-admin"]
    if args.all:
        reset_cmd.append("--all")
    
    print("[reset] Resetting users database...")
    exit_code, output = run_command(reset_cmd)
    if exit_code != 0:
        print(f"[reset] Error: reset-admin failed with exit code {exit_code}", file=sys.stderr)
        if output:
            print(f"[reset] Output: {output}", file=sys.stderr)
        return 1
    
    if args.interactive_admin:
        print("\n[reset] Setting up admin PIN interactively...")
        # Use the existing admin-pin script from init-scripts
        admin_pin_cmd = ["python3", "init-scripts/admin-pin.py"]
        exit_code, output = run_command(admin_pin_cmd)
        if exit_code != 0:
            print(f"[reset] Error: admin-pin setup failed with exit code {exit_code}", file=sys.stderr)
            if output:
                print(f"[reset] Output: {output}", file=sys.stderr)
            return 1
    
    print("\n[reset] Reset complete!")
    if args.interactive_admin:
        print("[reset] Admin PIN has been set up.")
    print("[reset] You can now log in as admin with your PIN.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
