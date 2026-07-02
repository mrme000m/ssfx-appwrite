#!/usr/bin/env python3
"""Create or locate the gold-market Perplexity Space."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pplx_agent import GoldMarketAgent, get_settings


def main() -> int:
    settings = get_settings()
    print(f"Ensuring space: {settings.gold_market_space_name}")
    agent = GoldMarketAgent(settings)
    try:
        uuid = agent._space.ensure_space()
        print(f"Space UUID: {uuid}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
