#!/usr/bin/env python3
"""Show status of local services and the Azure VM."""

import json
import shutil
import subprocess
import sys

from _config import AZURE_RESOURCE_GROUP, AZURE_VM_NAME


def run_az(args: list[str]) -> dict:
    cmd = ["az", "vm", *args, "--output", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running {' '.join(cmd)}:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout or "null")


def main() -> int:
    print("[dev] Local service status:")
    # TODO: add status checks for local services.

    print("\n[dev] Azure VM status:")
    if not shutil.which("az"):
        print("  Azure CLI (az) not found; skipping Azure VM status.", file=sys.stderr)
        return 0

    vm = run_az(
        [
            "show",
            "--name",
            AZURE_VM_NAME,
            "--resource-group",
            AZURE_RESOURCE_GROUP,
            "--show-details",
        ]
    )
    summary = {
        "Name": vm.get("name"),
        "ResourceGroup": vm.get("resourceGroup"),
        "Location": vm.get("location"),
        "Size": vm.get("hardwareProfile", {}).get("vmSize"),
        "PowerState": vm.get("powerState"),
        "PublicIp": vm.get("publicIps"),
        "ProvisioningState": vm.get("provisioningState"),
    }
    for key, value in summary.items():
        print(f"  {key}: {value}")

    print("\n[dev] All status checks completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
