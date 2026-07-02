#!/usr/bin/env python3
"""init-scripts/ctrader-oauth.py — Persist cTrader OAuth config to Appwrite Database."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml
from appwrite.client import Client
from appwrite.id import ID
from appwrite.services.tables_db import TablesDB
from appwrite.query import Query


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


def main():
    load_env()

    here = Path(__file__).parent
    config_file = here / "config.yml"
    if not config_file.exists():
        print(f"Error: {config_file} not found.", file=sys.stderr)
        sys.exit(1)

    with open(config_file) as f:
        cfg = yaml.safe_load(f)

    ctrader = cfg.get("ctrader", {})
    client_id = ctrader.get("client_id", "")
    client_secret = ctrader.get("client_secret", "")
    redirect_uri = ctrader.get("redirect_uri", "")
    environment = ctrader.get("environment", "")

    for key in ["client_id", "client_secret", "redirect_uri", "environment"]:
        if not ctrader.get(key):
            print(f"Error: ctrader.{key} is missing in {config_file}", file=sys.stderr)
            sys.exit(1)

    print("[init] Persisting cTrader OAuth config to Appwrite Database...")

    client = Client()
    client.set_endpoint(os.environ["APPWRITE_ENDPOINT"])
    client.set_project(os.environ["APPWRITE_PROJECT_ID"])
    client.set_key(os.environ["APPWRITE_API_KEY"])

    db = TablesDB(client)
    db_id = "ctrader_auth"

    # Store OAuth config in service_config table
    config_key = "ctrader_oauth"
    config_value = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "environment": environment,
    }

    try:
        # Check if config exists
        existing = None
        try:
            rows = db.list_rows(
                db_id,
                "service_config",
                [Query.equal("config_key", config_key)],
            )
            if rows.get("documents"):
                existing = rows["documents"][0]
        except Exception as e:
            print(f"[init] Config lookup warning: {e}")

        import json
        config_value_json = json.dumps(config_value)

        if not existing:
            db.create_row(
                db_id,
                "service_config",
                ID.unique(),
                {
                    "config_key": config_key,
                    "config_value": config_value_json,
                    "description": "cTrader OAuth configuration",
                    "updated_at": datetime.utcnow().isoformat(),
                },
            )
            print(f"[init] Created OAuth config with key '{config_key}'")
        else:
            db.update_row(
                db_id,
                "service_config",
                existing["$id"],
                {
                    "config_value": config_value_json,
                    "description": "cTrader OAuth configuration",
                    "updated_at": datetime.utcnow().isoformat(),
                },
            )
            print(f"[init] Updated OAuth config with key '{config_key}'")
    except Exception as e:
        print(f"Error: Config persistence failed: {e}", file=sys.stderr)
        sys.exit(1)

    print()
    print("[init] cTrader OAuth config persisted in Appwrite Database.")
    print(f"  client_id:    {client_id}")
    print(f"  redirect_uri: {redirect_uri}")
    print(f"  environment:  {environment}")
    print()
    print("IMPORTANT: The client_secret must be set as a Function variable.")
    print("Set the following environment variables and run ./dev.sh deploy-auth:")
    print("  CTRADER_CLIENT_SECRET=<secret>")
    print("  INTERNAL_API_KEY=<random>")
    print("  TOKEN_ENCRYPTION_KEY=<random>")
    print("  SESSION_HMAC_KEY=<random>")
    print()
    print("Then run:  ./dev.sh deploy-auth")


if __name__ == "__main__":
    main()
