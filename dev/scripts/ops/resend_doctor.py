#!/usr/bin/env python3
"""Check Resend CLI configuration."""
import shutil
import subprocess
import sys


def main() -> int:
    if not shutil.which("resend"):
        print(
            "Error: resend CLI is not installed. Install it with: npm install -g resend-cli",
            file=sys.stderr,
        )
        return 1

    print("[dev] Resend CLI status:")
    subprocess.run(["resend", "whoami"], check=True)

    print("\n[dev] Resend doctor:")
    subprocess.run(["resend", "doctor"], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
