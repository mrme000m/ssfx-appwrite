#!/usr/bin/env python3
"""Drop legacy `ctrader_auth` database and recreate `slwp_platform` from appwrite.config.json.

Uses the Appwrite CLI push tables command (reads appwrite.config.json) to create
the new schema, then re-seeds required system configuration.

Usage:
    ./dev.sh recreate-platform [--drop-only] [--seed-only]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.services.tables_db import TablesDB
from appwrite.exception import AppwriteException

LEGACY_DB = "ctrader_auth"
NEW_DB = "slwp_platform"


def make_client() -> Client:
    endpoint = os.environ.get("APPWRITE_ENDPOINT", "https://sgp.cloud.appwrite.io/v1")
    project = os.environ.get("APPWRITE_PROJECT_ID", "")
    key = os.environ.get("APPWRITE_API_KEY", "")
    if not project or not key:
        print("ERROR: APPWRITE_PROJECT_ID and APPWRITE_API_KEY required.", file=sys.stderr)
        sys.exit(1)
    c = Client()
    c.set_endpoint(endpoint)
    c.set_project(project)
    c.set_key(key)
    return c


def db_exists(databases: Databases, db_id: str) -> bool:
    try:
        databases.get(database_id=db_id)
        return True
    except AppwriteException as e:
        if e.code == 404:
            return False
        raise


def drop_database(databases: Databases, db_id: str) -> None:
    if not db_exists(databases, db_id):
        print(f"[skip] Database {db_id} does not exist.")
        return
    print(f"[drop] Deleting database {db_id} and all its tables...")
    databases.delete(database_id=db_id)
    print(f"[drop] Deleted database {db_id}")


def cli_login() -> None:
    endpoint = os.environ.get("APPWRITE_ENDPOINT", "https://sgp.cloud.appwrite.io/v1")
    project = os.environ.get("APPWRITE_PROJECT_ID", "")
    key = os.environ.get("APPWRITE_API_KEY", "")
    print("[cli] Logging in...")
    subprocess.run(
        ["appwrite", "client", "--endpoint", endpoint, "--project-id", project, "--key", key],
        check=True,
        capture_output=True,
    )
    print("[cli] Logged in.")


def push_tables() -> None:
    """Run appwrite push tables using appwrite.config.json which now has slwp_platform schema."""
    cli_login()
    print("[push] Running appwrite push tables --all --force ...")
    result = subprocess.run(
        ["appwrite", "push", "tables", "--all", "--force"],
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    print("[push] Tables pushed successfully.")


def seed_service_config(tables: TablesDB) -> None:
    """Insert minimal service_config rows needed for bootstrap."""
    seed_rows = [
        {
            "config_key": "ctrader_oauth",
            "config_value": json.dumps({
                "client_id": os.environ.get("CTRADER_CLIENT_ID", ""),
                "client_secret": os.environ.get("CTRADER_CLIENT_SECRET", ""),
                "redirect_uri": "https://auth.mrme.tech/callback",
                "environment": "demo",
            }),
            "config_json": "{}",
            "updated_at": "2024-01-01T00:00:00Z",
        },
        {
            "config_key": "master_auth",
            "config_value": json.dumps({"username": "admin", "role": "master"}),
            "config_json": "{}",
            "updated_at": "2024-01-01T00:00:00Z",
        },
        {
            "config_key": "pplx_agent",
            "config_value": json.dumps({
                "data_service_base_url": "http://localhost:9002",
                "perplexity_space_id": "",
            }),
            "config_json": "{}",
            "updated_at": "2024-01-01T00:00:00Z",
        },
    ]

    for row in seed_rows:
        try:
            tables.create_row(
                database_id=NEW_DB,
                table_id="service_config",
                row_id=row["config_key"],
                data=row,
            )
            print(f"[seed] Inserted service_config.{row['config_key']}")
        except AppwriteException as e:
            if e.code == 409:
                print(f"[seed] service_config.{row['config_key']} already exists")
            else:
                print(f"[warn] service_config insert error: {e.message}")


def exec_cli_json(cmd: list[str]) -> dict | None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def set_function_vars() -> None:
    """Update APPWRITE_DATABASE_ID on all 4 functions via CLI."""
    print("[vars] Updating APPWRITE_DATABASE_ID on Functions...")
    functions = ["auth-oauth", "auth-pin", "api-internal", "token-refresh"]
    for fn in functions:
        # First, check if variable exists
        existing = exec_cli_json(
            ["appwrite", "functions", "get-variable", "--function-id", fn, "--variable-key", "APPWRITE_DATABASE_ID", "--json"]
        )
        if existing:
            exec_cli_json(
                ["appwrite", "functions", "update-variable", "--function-id", fn, "--variable-key", "APPWRITE_DATABASE_ID", "--value", NEW_DB]
            )
            print(f"  [vars] Updated {fn}.APPWRITE_DATABASE_ID = {NEW_DB}")
        else:
            exec_cli_json(
                ["appwrite", "functions", "create-variable", "--function-id", fn, "--variable-key", "APPWRITE_DATABASE_ID", "--value", NEW_DB]
            )
            print(f"  [vars] Created {fn}.APPWRITE_DATABASE_ID = {NEW_DB}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop-only", action="store_true")
    parser.add_argument("--seed-only", action="store_true")
    args = parser.parse_args()

    client = make_client()
    databases = Databases(client)
    tables = TablesDB(client)

    if not args.seed_only:
        print("=" * 50)
        print("PHASE 1: Drop Legacy Database")
        print("=" * 50)
        drop_database(databases, LEGACY_DB)

    if not args.drop_only:
        print("\n" + "=" * 50)
        print("PHASE 2: Create/Verify slwp_platform")
        print("=" * 50)
        if not db_exists(databases, NEW_DB):
            databases.create(database_id=NEW_DB, name="slwp_platform")
            print(f"[migrate] Created database {NEW_DB}")
        else:
            print(f"[migrate] Database {NEW_DB} already exists.")

        print("\n" + "=" * 50)
        print("PHASE 3: Push Tables from appwrite.config.json")
        print("=" * 50)
        push_tables()

        print("\n" + "=" * 50)
        print("PHASE 4: Seed service_config")
        print("=" * 50)
        seed_service_config(tables)

        print("\n" + "=" * 50)
        print("PHASE 5: Update Function Variables")
        print("=" * 50)
        set_function_vars()

    print("\n" + "=" * 50)
    print("DONE")
    print("=" * 50)
    print(f"Database: {NEW_DB} is now source of truth.")
    print(f"Legacy database '{LEGACY_DB}' dropped.")
    print("Next: ./dev.sh deploy-auth to deploy updated Functions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
