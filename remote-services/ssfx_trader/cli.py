"""CLI for managing SSFX trading accounts."""
from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from .config import AccountConfig, CTraderConfig, PerAccountTradingConfig
from .stores.mongo_store import MongoAccountStore


def _env(key: str, default: str = "") -> str:
    val = os.getenv(key)
    return val if val and val.strip() else default


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="SSFX account manager")
    sub = parser.add_subparsers(dest="command")

    add = sub.add_parser("add", help="Add or update a trading account")
    add.add_argument("name", help="Account name")
    add.add_argument("--broker-url", default="https://auth-ctrader.mrme0.store")
    add.add_argument("--grant-id", default="")
    add.add_argument("--client-id", default="")
    add.add_argument("--client-secret", default="")
    add.add_argument("--account-id", type=int, default=0)
    add.add_argument("--host-type", choices=["live", "demo"], default="demo")
    add.add_argument("--volume", type=float, default=0.01)
    add.add_argument("--max-positions", type=int, default=3)
    add.add_argument("--mode", choices=["live", "demo"], default="demo")
    add.add_argument("--symbols", default="", help="Comma-separated symbol filter")
    add.add_argument("--enabled", action="store_true", default=True)
    add.add_argument("--disabled", action="store_true")

    sub.add_parser("list", help="List accounts")

    args = parser.parse_args()

    mongo_uri = _env("MONGODB_URI", "mongodb://localhost:27017")
    mongo_db = _env("MONGODB_DATABASE", "ssfx_v2")
    store = MongoAccountStore(mongo_uri, mongo_db, "_system")

    if args.command == "add":
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        cfg = AccountConfig(
            name=args.name,
            enabled=not args.disabled,
            ctrader=CTraderConfig(
                broker_url=args.broker_url,
                grant_id=args.grant_id,
                client_id=args.client_id,
                client_secret=args.client_secret,
                account_id=args.account_id,
                host_type=args.host_type,
            ),
            trading=PerAccountTradingConfig(
                execution_mode=args.mode,
                default_volume=args.volume,
                volume_value=args.volume,
                max_positions=args.max_positions,
            ),
            symbols_filter=symbols,
        )
        store.save_account(cfg)
        print(f"Account '{args.name}' saved.")
    elif args.command == "list":
        accounts = store.list_accounts()
        if not accounts:
            print("No accounts configured.")
            return
        for doc in accounts:
            print(f"- {doc['_id']}: enabled={doc.get('enabled')} mode={doc.get('trading', {}).get('execution_mode')}")
    else:
        parser.print_help()
