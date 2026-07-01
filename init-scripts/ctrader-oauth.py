#!/usr/bin/env python3
"""init-scripts/ctrader-oauth.py — Persist cTrader OAuth config to Appwrite Database."""

import os
import sys
from pathlib import Path

import yaml
from appwrite.client import Client
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
    config_row_id = "third_party_ctrader"

    # Upsert config row
    existing = None
    try:
        rows = db.list_rows(db_id, "slave_accounts", [
            Query.equal("grant_id", config_row_id),
        ])
        if rows.get("documents"):
            existing = rows["documents"][0]
    except Exception as e:
        print(f"[init] Row lookup warning: {e}")

    if not existing:
        try:
            db.create_row(
                db_id,
                "slave_accounts",
                config_row_id,
                {
                    "appwrite_user_id": "system",
                    "username": "_config",
                    "role": "master",
                    "grant_id": config_row_id,
                    "status": "active",
                    "active": False,
                    "email": "",
                    "access_token_enc": client_id,
                    "refresh_token_enc": redirect_uri,
                    "ctrader_account_ids": environment,
                },
            )
            print(f"[init] Created config row {config_row_id}")
        except Exception as e:
            print(f"Error: Row creation failed: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            db.update_row(
                db_id,
                "slave_accounts",
                existing["$id"],
                {
                    "access_token_enc": client_id,
                    "refresh_token_enc": redirect_uri,
                    "ctrader_account_ids": environment,
                },
            )
            print(f"[init] Updated config row {existing['$id']}")
        except Exception as e:
            print(f"Error: Row update failed: {e}", file=sys.stderr)
            sys.exit(1)

    print()
    print("[init] cTrader OAuth config persisted in Appwrite Database.")
    print(f"  client_id:    {client_id}")
    print(f"  redirect_uri: {redirect_uri}")
    print(f"  environment:  {environment}")
    print()
    print("IMPORTANT: The client_secret must be set as a Function variable.")
    print("Add it to the .env files in each function directory and push with --with-variables:")
    print("  functions/ctrader-auth/.env        → CTRADER_CLIENT_SECRET=<secret>")
    print("  functions/ctrader-internal/.env    → INTERNAL_API_KEY=<random>")
    print("  functions/ctrader-auth/.env        → TOKEN_ENCRYPTION_KEY=<random>")
    print("  functions/ctrader-auth/.env        → SESSION_HMAC_KEY=<random>")
    print()
    print("Then run:  ./dev.sh deploy-auth")


if __name__ == "__main__":
    main()
