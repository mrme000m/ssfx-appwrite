#!/usr/bin/env python3
"""Reset signal experience tables (use with caution)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from _config import load_env  # noqa: E402

REMOTE_SERVICES = Path(__file__).resolve().parent.parent.parent / "remote-services"
sys.path.insert(0, str(REMOTE_SERVICES))

from market_data_service.signal_experience.store import SignalExperienceStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset signal experience tables.")
    parser.add_argument(
        "--database-id",
        default=None,
        help="Appwrite database ID (default from APPWRITE_DATABASE_ID env)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    return parser.parse_args()


def main() -> int:
    load_env()
    args = parse_args()

    if not args.yes:
        confirm = input("This will delete all signal experience rows. Type 'yes' to continue: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            return 1

    store = SignalExperienceStore(database_id=args.database_id)
    store.reset_all()
    print("[signal-experience-reset] All experience tables cleared.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
