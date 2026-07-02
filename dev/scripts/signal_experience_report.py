#!/usr/bin/env python3
"""Generate signal experience insight reports for LLM agent context."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from _config import PROJECT_ROOT, load_env  # noqa: E402

REMOTE_SERVICES = PROJECT_ROOT / "remote-services"
sys.path.insert(0, str(REMOTE_SERVICES))

from market_data_service.signal_experience.reporter import DEFAULT_REPORT_DIR, generate_report  # noqa: E402
from market_data_service.signal_experience.store import SignalExperienceStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate signal experience reports.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="Output directory for reports",
    )
    parser.add_argument(
        "--database-id",
        default=None,
        help="Appwrite database ID (default from APPWRITE_DATABASE_ID env)",
    )
    return parser.parse_args()


def main() -> int:
    load_env()
    args = parse_args()

    store = SignalExperienceStore(database_id=args.database_id)
    paths = generate_report(store=store, output_dir=args.output)

    print("[signal-experience-report] Generated:")
    for name, path in paths.items():
        print(f"  {name}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
