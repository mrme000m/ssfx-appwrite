#!/usr/bin/env python3
"""Query the gold market Perplexity Space."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pplx_agent import GoldMarketAgent


async def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} '<question>' [--mode pro|deep_research|reasoning]", file=sys.stderr)
        return 1
    mode = "pro"
    if "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        mode = sys.argv[idx + 1]
        del sys.argv[idx : idx + 2]
    question = " ".join(sys.argv[1:])
    agent = GoldMarketAgent()
    result = await agent.query_space(question, mode=mode)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
