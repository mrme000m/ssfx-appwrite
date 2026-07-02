"""CLI entry point for the PPLX Agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import uvicorn

from .api import app
from .config import get_settings
from .gold_market_agent import GoldMarketAgent
from .logging import setup_logging
from .space_manager import SpaceManager

logger = setup_logging()


def _cmd_setup(args: argparse.Namespace) -> int:
    """Create the gold market Perplexity Space and print the UUID."""
    settings = get_settings()
    manager = SpaceManager(settings)
    try:
        uuid = manager.ensure_space()
        print(f"Space UUID: {uuid}")
        print(f"Space name: {settings.gold_market_space_name}")
        print("\nPersist this UUID to Appwrite service_config (config_key='pplx_agent',")
        print("path space.gold_market_uuid) or environment variable GOLD_MARKET_SPACE_UUID.")
        return 0
    except Exception as exc:
        logger.exception("Setup failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1


async def _cmd_update(args: argparse.Namespace) -> int:
    agent = GoldMarketAgent()
    try:
        result = await agent.run_daily_update()
        print(json.dumps(result, indent=2, default=str))
        return 0
    except Exception as exc:
        logger.exception("Daily update failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _cmd_query(args: argparse.Namespace) -> int:
    agent = GoldMarketAgent()
    try:
        result = asyncio.run(agent.query_space(" ".join(args.query), mode=args.mode))
        print(json.dumps(result, indent=2, default=str))
        return 0
    except Exception as exc:
        logger.exception("Query failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _cmd_server(args: argparse.Namespace) -> int:
    settings = get_settings()
    uvicorn.run(
        app,
        host=args.host or settings.api_host,
        port=args.port or settings.api_port,
        log_level="info",
    )
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    agent = GoldMarketAgent()
    try:
        limits = agent.get_rate_limits()
        print(json.dumps(limits, indent=2, default=str))
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pplx-agent", description="Perplexity-powered gold market intelligence")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="Create/ensure the gold market Perplexity Space")

    update_parser = sub.add_parser("update", help="Run the daily gold market update")
    update_parser.set_defaults(func=lambda a: asyncio.run(_cmd_update(a)))

    query_parser = sub.add_parser("query", help="Query the gold market knowledge base")
    query_parser.add_argument("query", nargs="+", help="Question to ask")
    query_parser.add_argument("--mode", default="pro", help="Perplexity mode")
    query_parser.set_defaults(func=_cmd_query)

    server_parser = sub.add_parser("server", help="Start the API server")
    server_parser.add_argument("--host", default=None, help="Bind host")
    server_parser.add_argument("--port", type=int, default=None, help="Bind port")
    server_parser.set_defaults(func=_cmd_server)

    sub.add_parser("status", help="Show Perplexity rate-limit status").set_defaults(func=_cmd_status)

    # setup command is sync
    sub.choices["setup"].set_defaults(func=_cmd_setup)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
