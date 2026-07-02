#!/usr/bin/env python3
"""
dev/scripts/init-ctrader-tables.py — Idempotent table creation for ctrader_auth database.

Creates or updates TablesDB tables that the cTrader + SSFX runtime services expect.
Safe to re-run: existing tables and columns are skipped.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure we can import appwrite_sdk helpers if needed
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / "remote-services"))

from appwrite.client import Client
from appwrite.services.tables_db import TablesDB

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATABASE_ID = "ctrader_auth"

ENDPOINT = os.getenv("APPWRITE_ENDPOINT", "https://sgp.cloud.appwrite.io/v1")
PROJECT_ID = os.getenv("APPWRITE_PROJECT_ID", "6a22a362002b9ae880bb")
API_KEY = os.getenv("APPWRITE_API_KEY", "")

if not API_KEY:
    print("ERROR: APPWRITE_API_KEY must be set in environment or .env")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Column specs: (key, type, required, extra_kwargs)
# ---------------------------------------------------------------------------
TABLES: dict[str, list[dict]] = {
    "ssfx_accounts": [
        {"key": "name", "type": "varchar", "required": True, "size": 64},
        {"key": "enabled", "type": "boolean", "required": False},
        {"key": "owner_id", "type": "varchar", "required": False, "size": 64},
        {"key": "host_type", "type": "varchar", "required": False, "size": 16},
        {"key": "config_json", "type": "text", "required": False},
    ],
    "ssfx_executions": [
        {"key": "account_name", "type": "varchar", "required": True, "size": 64},
        {"key": "chat_id", "type": "varchar", "required": True, "size": 64},
        {"key": "message_id", "type": "varchar", "required": True, "size": 32},
        {"key": "signal_type", "type": "varchar", "required": True, "size": 16},
        {"key": "status", "type": "varchar", "required": True, "size": 32},
        {"key": "order_id", "type": "varchar", "required": False, "size": 64},
        {"key": "position_id", "type": "varchar", "required": False, "size": 64},
        {"key": "executed_price", "type": "double", "required": False},
        {"key": "volume", "type": "double", "required": False},
        {"key": "original_volume_lots", "type": "double", "required": False},
        {"key": "error", "type": "text", "required": False},
        {"key": "skip_reason", "type": "text", "required": False},
        {"key": "updated_at", "type": "datetime", "required": False},
    ],
}

INDEXES: dict[str, list[dict]] = {
    "ssfx_accounts": [
        {"key": "idx_name", "type": "unique", "columns": ["name"]},
    ],
    "ssfx_executions": [
        {"key": "idx_account_msg", "type": "unique", "columns": ["account_name", "chat_id", "message_id"]},
    ],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_tables_db() -> TablesDB:
    client = Client()
    client.set_endpoint(ENDPOINT)
    client.set_project(PROJECT_ID)
    client.set_key(API_KEY)
    return TablesDB(client)


def _get_id(obj) -> str:
    if isinstance(obj, dict):
        return obj.get("$id", "")
    return getattr(obj, "$id", "") or getattr(obj, "id", "")


def _get_key(obj) -> str:
    if isinstance(obj, dict):
        return obj.get("key", "")
    return getattr(obj, "key", "")


def list_existing_tables(tables_db: TablesDB) -> set[str]:
    result = tables_db.list_tables(database_id=DATABASE_ID)
    return {_get_id(t) for t in getattr(result, "tables", [])}


def list_existing_columns(tables_db: TablesDB, table_id: str) -> set[str]:
    result = tables_db.list_columns(database_id=DATABASE_ID, table_id=table_id)
    return {_get_key(c) for c in getattr(result, "columns", [])}


def list_existing_indexes(tables_db: TablesDB, table_id: str) -> set[str]:
    result = tables_db.list_indexes(database_id=DATABASE_ID, table_id=table_id)
    return {_get_key(i) for i in getattr(result, "indexes", [])}


def create_table_if_missing(tables_db: TablesDB, table_id: str, name: str) -> bool:
    existing = list_existing_tables(tables_db)
    if table_id in existing:
        print(f"  • Table '{table_id}' already exists — skipping creation")
        return False
    tables_db.create_table(database_id=DATABASE_ID, table_id=table_id, name=name)
    print(f"  ✓ Created table '{table_id}'")
    return True


def create_column(tables_db: TablesDB, table_id: str, spec: dict) -> None:
    key = spec["key"]
    col_type = spec["type"]
    required = spec["required"]

    kwargs = {"database_id": DATABASE_ID, "table_id": table_id, "key": key, "required": required}
    kwargs.update({k: v for k, v in spec.items() if k not in ("key", "type", "required")})

    if col_type == "varchar":
        tables_db.create_varchar_column(**kwargs)
    elif col_type == "text":
        tables_db.create_text_column(**kwargs)
    elif col_type == "boolean":
        tables_db.create_boolean_column(**kwargs)
    elif col_type == "double":
        tables_db.create_float_column(**kwargs)
    elif col_type == "datetime":
        tables_db.create_datetime_column(**kwargs)
    elif col_type == "integer":
        tables_db.create_integer_column(**kwargs)
    elif col_type == "bigint":
        tables_db.create_big_int_column(**kwargs)
    else:
        print(f"    ⚠ Unknown column type '{col_type}' for '{key}' — skipping")
        return
    print(f"  ✓ Added column '{key}' ({col_type})")


def create_columns(tables_db: TablesDB, table_id: str, specs: list[dict]) -> None:
    existing = list_existing_columns(tables_db, table_id)
    for spec in specs:
        key = spec["key"]
        if key in existing:
            print(f"  • Column '{key}' already exists — skipping")
            continue
        try:
            create_column(tables_db, table_id, spec)
        except Exception as exc:
            print(f"  ✗ Failed to create column '{key}': {exc}")


def create_indexes(tables_db: TablesDB, table_id: str, specs: list[dict]) -> None:
    existing = list_existing_indexes(tables_db, table_id)
    for spec in specs:
        key = spec["key"]
        if key in existing:
            print(f"  • Index '{key}' already exists — skipping")
            continue
        try:
            tables_db.create_index(
                database_id=DATABASE_ID,
                table_id=table_id,
                key=key,
                type=spec["type"],
                columns=spec["columns"],
            )
            print(f"  ✓ Created index '{key}' ({spec['type']})")
        except Exception as exc:
            print(f"  ✗ Failed to create index '{key}': {exc}")


def table_exists(tables_db: TablesDB, table_id: str) -> bool:
    return table_id in list_existing_tables(tables_db)


def ensure_column(tables_db: TablesDB, table_id: str, spec: dict) -> None:
    """Idempotent single-column add (used for patching existing tables)."""
    existing = list_existing_columns(tables_db, table_id)
    key = spec["key"]
    if key in existing:
        return
    try:
        create_column(tables_db, table_id, spec)
    except Exception as exc:
        print(f"  ✗ Failed to add column '{key}' to '{table_id}': {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    print("=" * 60)
    print("cTrader Auth Tables — Idempotent Schema Setup")
    print("=" * 60)
    print(f"Database: {DATABASE_ID}")
    print(f"Endpoint: {ENDPOINT}")
    print()

    tables_db = get_tables_db()

    # Ensure tables exist
    for table_id, name in [
        ("ssfx_accounts", "SSFX Accounts"),
        ("ssfx_executions", "SSFX Executions"),
    ]:
        print(f"\n[Table: {table_id}]")
        created = create_table_if_missing(tables_db, table_id, name)
        if not created and not table_exists(tables_db, table_id):
            print(f"  ✗ Table '{table_id}' does not exist and could not be created")
            continue

        columns = TABLES.get(table_id, [])
        if columns:
            create_columns(tables_db, table_id, columns)

        indexes = INDEXES.get(table_id, [])
        if indexes:
            create_indexes(tables_db, table_id, indexes)

    print("\n" + "=" * 60)
    print("Schema setup complete.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
