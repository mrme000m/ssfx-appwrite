#!/usr/bin/env python3
"""dev/scripts/init/ctrader-trading-events.py — Create the ctrader_trading_events TablesDB table."""

import os
from pathlib import Path

import yaml
from appwrite.client import Client
from appwrite.exception import AppwriteException
from appwrite.services.tables_db import TablesDB


def load_env():
    root = Path(__file__).parent.parent
    env_file = root / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key, val)


def _already_exists(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "already exists" in msg or "column keys must be unique" in msg


def ensure_column(db: TablesDB, db_id: str, table_id: str, key: str, col_type: str, **kwargs):
    try:
        db.get_column(db_id, table_id, key)
        print(f"  column '{key}' exists")
        return
    except AppwriteException as exc:
        if _already_exists(exc):
            print(f"  column '{key}' exists")
            return
    except Exception:
        pass

    print(f"  creating column '{key}' ({col_type})")
    factory = getattr(db, f"create_{col_type}_column")
    try:
        factory(db_id, table_id, key, **kwargs)
    except AppwriteException as exc:
        if _already_exists(exc):
            print(f"  column '{key}' exists")
            return
        raise


def main():
    load_env()

    here = Path(__file__).parent
    config_file = here / "config.yml"
    db_id = "slwp_platform"
    if config_file.exists():
        with open(config_file) as f:
            cfg = yaml.safe_load(f) or {}
        db_id = cfg.get("ctrader", {}).get("database_id", db_id)

    table_id = "trading_events"

    client = Client()
    client.set_endpoint(os.environ["APPWRITE_ENDPOINT"])
    client.set_project(os.environ["APPWRITE_PROJECT_ID"])
    client.set_key(os.environ["APPWRITE_API_KEY"])

    db = TablesDB(client)

    print(f"[init] Ensuring table '{table_id}' exists in database '{db_id}'...")

    try:
        db.get_table(db_id, table_id)
        print(f"[init] Table '{table_id}' already exists")
    except Exception:
        print(f"[init] Creating table '{table_id}'...")
        db.create_table(
            database_id=db_id,
            table_id=table_id,
            name="cTrader Trading Events",
            permissions=[
                'create("any")',
                'read("users")',
            ],
            row_security=False,
            enabled=True,
        )

    ensure_column(db, db_id, table_id, "grant_id", "varchar", size=255, required=True)
    ensure_column(db, db_id, table_id, "ctid_trader_account_id", "varchar", size=32, required=True)
    ensure_column(db, db_id, table_id, "event_type", "varchar", size=64, required=True)
    ensure_column(db, db_id, table_id, "payload", "longtext", required=True)
    ensure_column(db, db_id, table_id, "created_at", "varchar", size=64, required=True)

    print(f"[init] Table '{table_id}' is ready.")


if __name__ == "__main__":
    main()
