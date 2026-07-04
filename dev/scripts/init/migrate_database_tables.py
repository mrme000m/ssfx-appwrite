#!/usr/bin/env python3
"""Migrate Appwrite TablesDB from legacy `ctrader_auth` database to `slwp_platform`.

Usage:
    export APPWRITE_ENDPOINT=https://sgp.cloud.appwrite.io/v1
    export APPWRITE_PROJECT_ID=6a22a362002b9ae880bb
    export APPWRITE_API_KEY=<api-key>
    python3 dev/scripts/init/migrate_database_tables.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.services.tables_db import TablesDB
from appwrite.exception import AppwriteException

LEGACY_DB = "ctrader_auth"
NEW_DB = "slwp_platform"

TABLE_MAP: dict[str, str] = {
    "slave_accounts": "users",
    "accounts": "ctrader_accounts",
    "trade_configs": "trade_settings",
    "ssfx_accounts": "signal_slaves",
    "account_events": "account_state_history",
    "master_signals": "signal_broadcasts",
    "ssfx_executions": "signal_executions",
    "ctrader_trading_events": "trading_events",
    "ephemeral_tokens": "ephemeral_tokens",
    "grant_locks": "grant_locks",
    "service_config": "service_config",
    "ssfx_presets": "trade_presets",
    "ssfx_risk_state": "risk_state",
}

NO_COPY_TABLES = {"ephemeral_tokens", "grant_locks"}


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


def table_exists(tables: TablesDB, db_id: str, table_id: str) -> bool:
    try:
        tables.get_table(database_id=db_id, table_id=table_id)
        return True
    except AppwriteException as e:
        if e.code == 404:
            return False
        raise


def create_db(databases: Databases, db_id: str, name: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] Would create database: {db_id}")
        return
    try:
        databases.create(database_id=db_id, name=name)
        print(f"[migrate] Created database: {db_id}")
    except AppwriteException as e:
        if e.code == 409:
            print(f"[migrate] Database {db_id} already exists.")
        else:
            raise


def create_table(tables: TablesDB, db_id: str, table_id: str, name: str, row_security: bool, permissions: list | None, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] Would create table: {db_id}.{table_id}")
        return
    try:
        tables.create_table(
            database_id=db_id,
            table_id=table_id,
            name=name,
            row_security=row_security,
            permissions=permissions or [],
        )
        print(f"[migrate] Created table: {db_id}.{table_id}")
    except AppwriteException as e:
        if e.code == 409:
            print(f"[migrate] Table {db_id}.{table_id} already exists.")
        else:
            raise


def add_column(tables: TablesDB, db_id: str, table_id: str, col: dict, dry_run: bool) -> None:
    cid = col.get("key", "")
    if not cid or cid.startswith("$"):
        return
    ctype = col.get("type", "string")
    if dry_run:
        print(f"[dry-run] Would add column {table_id}.{cid}: {ctype}")
        return

    required = col.get("required", False)
    default = col.get("default")
    array = col.get("array", False)
    min_val = col.get("min")
    max_val = col.get("max")

    try:
        if ctype == "varchar":
            size = int(col.get("size", 255)) if col.get("size") is not None else 255
            tables.create_varchar_column(
                database_id=db_id, table_id=table_id, key=cid, size=size,
                required=required, default=default, array=array,
            )
        elif ctype in ("text", "mediumtext"):
            tables.create_text_column(
                database_id=db_id, table_id=table_id, key=cid,
                required=required, default=default, array=array,
            )
        elif ctype == "longtext":
            tables.create_longtext_column(
                database_id=db_id, table_id=table_id, key=cid,
                required=required, default=default, array=array,
            )
        elif ctype == "boolean":
            tables.create_boolean_column(
                database_id=db_id, table_id=table_id, key=cid,
                required=required, default=default, array=array,
            )
        elif ctype == "datetime":
            tables.create_datetime_column(
                database_id=db_id, table_id=table_id, key=cid,
                required=required, default=default, array=array,
            )
        elif ctype == "integer":
            tables.create_integer_column(
                database_id=db_id, table_id=table_id, key=cid,
                required=required, min=min_val, max=max_val, default=default, array=array,
            )
        elif ctype == "double":
            tables.create_float_column(
                database_id=db_id, table_id=table_id, key=cid,
                required=required, min=min_val, max=max_val, default=default, array=array,
            )
        elif ctype == "email":
            tables.create_email_column(
                database_id=db_id, table_id=table_id, key=cid,
                required=required, default=default, array=array,
            )
        elif ctype == "enum":
            elements = col.get("elements", [])
            tables.create_enum_column(
                database_id=db_id, table_id=table_id, key=cid,
                required=required, elements=elements, default=default, array=array,
            )
        elif ctype == "url":
            tables.create_url_column(
                database_id=db_id, table_id=table_id, key=cid,
                required=required, default=default, array=array,
            )
        else:
            # Fallback: create as mediumtext
            print(f"  [warn] Unknown column type '{ctype}' for {cid}, using text")
            tables.create_text_column(
                database_id=db_id, table_id=table_id, key=cid,
                required=required, default=default, array=array,
            )
    except AppwriteException as e:
        if e.code == 409:
            print(f"  [skip] Column {cid} already exists")
        else:
            print(f"  [warn] Column {cid} error: {e.message}")


def copy_rows(tables: TablesDB, old_db: str, old_table: str, new_db: str, new_table: str, dry_run: bool) -> int:
    if dry_run:
        print(f"[dry-run] Would copy rows {old_db}.{old_table} → {new_db}.{new_table}")
        return 0
    total = 0
    offset = 0
    limit = 100
    while True:
        try:
            resp = tables.list_rows(database_id=old_db, table_id=old_table, queries=[], limit=limit, offset=offset)
        except AppwriteException as e:
            print(f"  [warn] list_rows error: {e.message}")
            break
        items = resp.dict().get("rows", resp.dict().get("documents", []))
        if not items:
            break
        for item in items:
            row_id = item.get("$id")
            raw = item.get("data", item)
            clean = {k: v for k, v in raw.items() if not k.startswith("$")}
            perms = item.get("$permissions", [])
            try:
                tables.create_row(
                    database_id=new_db,
                    table_id=new_table,
                    row_id=row_id,
                    data=clean,
                    permissions=perms,
                )
                total += 1
            except AppwriteException as e:
                if e.code == 409:
                    # Row already exists — try update
                    try:
                        tables.update_row(
                            database_id=new_db,
                            table_id=new_table,
                            row_id=row_id,
                            data=clean,
                            permissions=perms,
                        )
                        total += 1
                    except AppwriteException as e2:
                        print(f"  [warn] Row {row_id} update failed: {e2.message}")
                else:
                    print(f"  [warn] Row {row_id} create failed: {e.message}")
        offset += limit
        if len(items) < limit:
            break
    print(f"  [migrate] Copied {total} rows {old_db}.{old_table} → {new_db}.{new_table}")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate TablesDB to new naming")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    args = parser.parse_args()
    dry_run = args.dry_run

    client = make_client()
    databases = Databases(client)
    tables = TablesDB(client)

    print("=" * 60)
    print("Appwrite TablesDB Migration")
    print(f"  Legacy DB: {LEGACY_DB}")
    print(f"  New DB:    {NEW_DB}")
    print("=" * 60)

    if not db_exists(databases, LEGACY_DB):
        print(f"ERROR: Legacy database '{LEGACY_DB}' not found.", file=sys.stderr)
        return 1

    create_db(databases, NEW_DB, "slwp_platform", dry_run)

    for old_name, new_name in TABLE_MAP.items():
        if not table_exists(tables, LEGACY_DB, old_name):
            print(f"[skip] Old table {LEGACY_DB}.{old_name} not found.")
            continue

        old_spec = tables.get_table(database_id=LEGACY_DB, table_id=old_name).dict()

        if table_exists(tables, NEW_DB, new_name):
            print(f"[skip] New table {NEW_DB}.{new_name} already exists.")
        else:
            create_table(
                tables, NEW_DB, new_name,
                name=new_name.replace("_", " ").title(),
                row_security=old_spec.get("rowsecurity", False),
                permissions=old_spec.get("permissions", []),
                dry_run=dry_run,
            )
            for col in old_spec.get("columns", []):
                col_d = col if isinstance(col, dict) else col.dict()
                add_column(tables, NEW_DB, new_name, col_d, dry_run)

        if old_name in NO_COPY_TABLES:
            print(f"[skip] Table {old_name} marked NO_COPY.")
            continue

        copy_rows(tables, LEGACY_DB, old_name, NEW_DB, new_name, dry_run)

    print("=" * 60)
    print("Migration complete.")
    if dry_run:
        print("Run again without --dry-run to execute.")
    else:
        print("Next steps:")
        print(f"  1. Set APPWRITE_DATABASE_ID={NEW_DB} in function vars and v2.env")
        print(f"  2. Deploy updated Functions")
        print(f"  3. Validate with smoke tests")
        print(f"  4. After validation: drop old database '{LEGACY_DB}'")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
