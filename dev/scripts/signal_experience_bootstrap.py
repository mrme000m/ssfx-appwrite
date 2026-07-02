#!/usr/bin/env python3
"""Bootstrap signal experience tables from historical signal JSON."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from _config import PROJECT_ROOT, load_env  # noqa: E402

REMOTE_SERVICES = PROJECT_ROOT / "remote-services"
sys.path.insert(0, str(REMOTE_SERVICES))

from market_data_service.signal_experience.bootstrap import bootstrap  # noqa: E402
from market_data_service.signal_experience.store import SignalExperienceStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap signal experience tables from history.")
    parser.add_argument(
        "--input",
        type=Path,
        default=PROJECT_ROOT / "signals_raw_180d.json",
        help="Path to raw signals JSON",
    )
    parser.add_argument(
        "--database-id",
        default=None,
        help="Appwrite database ID (default from APPWRITE_DATABASE_ID env)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset experience tables before bootstrap",
    )
    return parser.parse_args()


def main() -> int:
    load_env()
    args = parse_args()

    if not args.input.exists():
        print(f"[signal-experience-bootstrap] Input file not found: {args.input}", file=sys.stderr)
        return 1

    store = SignalExperienceStore(database_id=args.database_id)
    result = bootstrap(args.input, store=store, reset=args.reset)

    print("[signal-experience-bootstrap] Complete:")
    for key, value in result.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
