#!/usr/bin/env python3
"""Helper to resolve the deployment Azure VM details."""
import json
import subprocess
import sys

from _config import AZURE_RESOURCE_GROUP, AZURE_VM_NAME


def run_az(args: list[str]) -> dict:
    """Run an az CLI command and return parsed JSON."""
    cmd = ["az", "vm", *args, "--output", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running {' '.join(cmd)}:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout or "null")


def get_power_state() -> str:
    view = run_az(
        [
            "get-instance-view",
            "--name",
            AZURE_VM_NAME,
            "--resource-group",
            AZURE_RESOURCE_GROUP,
        ]
    )
    for status in view.get("instanceView", {}).get("statuses", []):
        code = status.get("code", "")
        if "PowerState/" in code:
            return status.get("displayStatus", "unknown")
    return "unknown"


def get_public_ip() -> str:
    result = subprocess.run(
        [
            "az",
            "vm",
            "list-ip-addresses",
            "--name",
            AZURE_VM_NAME,
            "--resource-group",
            AZURE_RESOURCE_GROUP,
            "--query",
            "[0].virtualMachine.network.publicIpAddresses[0].ipAddress",
            "--output",
            "tsv",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error resolving public IP:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    ip = result.stdout.strip()
    if not ip or ip == "null":
        print(f"Error: could not resolve public IP for {AZURE_VM_NAME}", file=sys.stderr)
        sys.exit(1)
    return ip


def main() -> int:
    power_state = get_power_state()
    if power_state != "VM running":
        print(
            f"Error: Azure VM {AZURE_VM_NAME} is not running (state: {power_state}).",
            file=sys.stderr,
        )
        return 1

    public_ip = get_public_ip()
    print(f"{AZURE_VM_NAME} {public_ip}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
