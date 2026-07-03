#!/usr/bin/env python3
"""Send an email via Resend CLI."""

import os
import shutil
import subprocess
import sys
import time

from _config import RESEND_FROM_EMAIL


def main() -> int:
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <to> <subject> <body> [from]", file=sys.stderr)
        return 1

    to = sys.argv[1]
    subject = sys.argv[2]
    body = sys.argv[3]
    from_addr = (
        sys.argv[4]
        if len(sys.argv) > 4
        else os.getenv("RESEND_FROM_EMAIL", RESEND_FROM_EMAIL)
    )

    if not shutil.which("resend"):
        print(
            "Error: resend CLI is not installed. Install it with: npm install -g resend-cli",
            file=sys.stderr,
        )
        return 1

    idempotency_key = f"dev-send-{int(time.time())}-{to}"

    print("[dev] Sending email via Resend...")
    subprocess.run(
        [
            "resend",
            "emails",
            "send",
            "--from",
            from_addr,
            "--to",
            to,
            "--subject",
            subject,
            "--text",
            body,
            "--idempotency-key",
            idempotency_key,
        ],
        check=True,
    )
    print("[dev] Email sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
