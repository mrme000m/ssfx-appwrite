#!/usr/bin/env python3
"""Run the daily gold market update."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pplx_agent import GoldMarketAgent


async def main() -> int:
    agent = GoldMarketAgent()
    result = await agent.run_daily_update()
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("report_path") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
