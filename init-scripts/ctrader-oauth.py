#!/usr/bin/env python3
"""init-scripts/ctrader-oauth.py — Persist cTrader OAuth config to Appwrite TablesDB."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from appwrite.client import Client
from appwrite.id import ID
from appwrite.query import Query
from appwrite.services.tables_db import TablesDB

from _env import load_env

DB_ID = "ctrader_auth"
SERVICE_CONFIG_KEY = "ctrader_oauth"


def read_config() -> dict[str, str]:
    here = Path(__file__).parent
    config_file = here / "config.yml"
    if not config_file.exists():
        print(f"Error: {config_file} not found.", file=sys.stderr)
        sys.exit(1)
    with open(config_file, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("ctrader", {})


def validate_config(ctrader: dict[str, str]) -> None:
    for key in ("client_id", "client_secret", "redirect_uri", "environment"):
        if not ctrader.get(key):
            print(f"Error: ctrader.{key} is missing in config.yml", file=sys.stderr)
            sys.exit(1)


def build_tables_db() -> TablesDB:
    client = Client()
    client.set_endpoint(os.environ["APPWRITE_ENDPOINT"])
    client.set_project(os.environ["APPWRITE_PROJECT_ID"])
    client.set_key(os.environ["APPWRITE_API_KEY"])
    return TablesDB(client)


def find_existing(db: TablesDB) -> dict | None:
    try:
        result = db.list_rows(
            database_id=DB_ID,
            table_id="service_config",
            queries=[Query.equal("config_key", SERVICE_CONFIG_KEY)],
        )
        rows = getattr(result, "documents", getattr(result, "rows", []))
        if rows:
            return rows[0]
    except Exception as exc:
        print(f"[init] Config lookup warning: {exc}")
    return None


def main() -> int:
    load_env()

    ctrader = read_config()
    validate_config(ctrader)

    print("[init] Persisting cTrader OAuth config to Appwrite TablesDB...")
    db = build_tables_db()

    config_value = {
        "client_id": ctrader["client_id"],
        "client_secret": ctrader["client_secret"],
        "redirect_uri": ctrader["redirect_uri"],
        "environment": ctrader["environment"],
    }

    body = {
        "config_key": SERVICE_CONFIG_KEY,
        "config_value": json.dumps(config_value),
        "description": "cTrader OAuth configuration",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    existing = find_existing(db)
    if not existing:
        db.create_row(
            database_id=DB_ID,
            table_id="service_config",
            row_id=ID.unique(),
            data=body,
        )
        print(f"[init] Created OAuth config row with key '{SERVICE_CONFIG_KEY}'")
    else:
        row_id = existing.get("$id", getattr(existing, "id", None))
        db.update_row(
            database_id=DB_ID,
            table_id="service_config",
            row_id=row_id,
            data=body,
        )
        print(f"[init] Updated OAuth config row '{row_id}'")

    print()
    print("[init] cTrader OAuth config persisted in Appwrite Database.")
    print(f"  client_id:    {ctrader['client_id']}")
    print(f"  redirect_uri: {ctrader['redirect_uri']}")
    print(f"  environment:  {ctrader['environment']}")
    print()
    print("IMPORTANT: The client_secret must be available to the Appwrite Functions.")
    print("Set the following environment variables and run ./dev.sh deploy-auth:")
    print("  CTRADER_CLIENT_SECRET=<secret>")
    print("  INTERNAL_API_KEY=<random>")
    print("  TOKEN_ENCRYPTION_KEY=<random>")
    print("  SESSION_HMAC_KEY=<random>")
    print()
    print("Then run:  ./dev.sh deploy-auth")
    return 0


if __name__ == "__main__":
    sys.exit(main())
