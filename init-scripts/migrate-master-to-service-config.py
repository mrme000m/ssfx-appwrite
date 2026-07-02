#!/usr/bin/env python3
"""init-scripts/migrate-master-to-service-config.py

One-off migration: move the existing master admin row from slave_accounts
into service_config (master_auth key) so slave_accounts holds only slave
identity + encrypted tokens.

Idempotent: safe to rerun; only acts if a master row exists in slave_accounts.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from appwrite.client import Client
from appwrite.id import ID
from appwrite.query import Query
from appwrite.services.tables_db import TablesDB

from _env import load_env

DB_ID = "ctrader_auth"
MASTER_AUTH_KEY = "master_auth"


def build_db() -> TablesDB:
    client = Client()
    client.set_endpoint(os.environ["APPWRITE_ENDPOINT"])
    client.set_project(os.environ["APPWRITE_PROJECT_ID"])
    client.set_key(os.environ["APPWRITE_API_KEY"])
    return TablesDB(client)


def _row_id(row) -> str | None:
    if hasattr(row, "get"):
        return row.get("$id")
    return getattr(row, "$id", getattr(row, "id", None))


def main() -> int:
    load_env()
    db = build_db()

    try:
        result = db.list_rows(
            database_id=DB_ID,
            table_id="slave_accounts",
            queries=[Query.equal("role", "master")],
        )
        rows = getattr(result, "documents", getattr(result, "rows", []))
    except Exception as exc:
        print(f"[migrate] Failed to list master rows: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print("[migrate] No master row found in slave_accounts")
        return 0

    old = rows[0]
    old_id = old.get("$id", getattr(old, "id", None))
    body = {
        "config_key": MASTER_AUTH_KEY,
        "config_value": json.dumps({
            "appwrite_user_id": old.get("appwrite_user_id"),
            "username": old.get("username", "admin"),
            "email": old.get("email", ""),
            "pin_hash": old.get("pin_hash", ""),
            "role": "master",
            "active": old.get("active", True),
        }),
        "description": "Master admin authentication record",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        existing = db.list_rows(
            database_id=DB_ID,
            table_id="service_config",
            queries=[Query.equal("config_key", MASTER_AUTH_KEY)],
        )
        existing_rows = getattr(existing, "documents", getattr(existing, "rows", []))
        if existing_rows:
            row_id = _row_id(existing_rows[0])
            db.update_row(database_id=DB_ID, table_id="service_config", row_id=row_id, data=body)
            print(f"[migrate] Updated service_config row '{row_id}'")
        else:
            db.create_row(database_id=DB_ID, table_id="service_config", row_id=ID.unique(), data=body)
            print("[migrate] Created service_config master_auth row")

        db.delete_row(database_id=DB_ID, table_id="slave_accounts", row_id=old_id)
        print(f"[migrate] Deleted stale master row '{old_id}' from slave_accounts")
    except Exception as exc:
        print(f"[migrate] Migration failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
