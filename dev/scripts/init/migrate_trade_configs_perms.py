#!/usr/bin/env python3
"""
migrate_trade_configs_perms.py

One-off migration: add row-level user permissions to existing trade_configs rows
that were created while the table had broad table-level permissions.

Usage:
    ./dev.sh migrate-trade-configs-perms

The script is idempotent and safe to rerun.
"""

import os
import sys
from pathlib import Path

from appwrite.client import Client
from appwrite.services.tables_db import TablesDB
from appwrite.permission import Permission
from appwrite.role import Role
from appwrite.query import Query


def load_env():
    root = Path(__file__).parent.parent.parent
    env_file = root / ".env"
    if not env_file.exists():
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


def main():
    load_env()

    required = ["APPWRITE_ENDPOINT", "APPWRITE_PROJECT_ID", "APPWRITE_API_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"Error: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    client = (
        Client()
        .set_endpoint(os.environ["APPWRITE_ENDPOINT"])
        .set_project(os.environ["APPWRITE_PROJECT_ID"])
        .set_key(os.environ["APPWRITE_API_KEY"])
    )
    db = TablesDB(client)
    db_id = "ctrader_auth"
    table_id = "trade_settings"

    print("[migrate] Fetching trade_configs rows...")
    offset = 0
    limit = 100
    updated = 0
    skipped = 0
    errors = 0

    while True:
        try:
            result = db.list_rows(db_id, table_id, [Query.limit(limit), Query.offset(offset)])
        except Exception as e:
            print(f"[migrate] Failed to list rows: {e}", file=sys.stderr)
            sys.exit(1)

        rows = getattr(result, "rows", getattr(result, "documents", []))
        if not rows:
            break

        for row in rows:
            # Row may be a dict or a Pydantic model depending on SDK version.
            def _get(key, default=None):
                if isinstance(row, dict):
                    return row.get(key, default)
                return getattr(row, key, default)

            user_id = _get("slave_user_id")
            if not user_id:
                skipped += 1
                continue

            perms = _get("$permissions", [])
            row_id = _get("$id")
            has_user_perm = any(
                f'user("{user_id}")' in p or f"user('{user_id}')" in p
                for p in perms
            )
            if has_user_perm:
                skipped += 1
                continue

            try:
                db.update_row(
                    db_id,
                    table_id,
                    row_id,
                    {},
                    permissions=[
                        Permission.read(Role.user(user_id)),
                        Permission.update(Role.user(user_id)),
                        Permission.delete(Role.user(user_id)),
                    ],
                )
                updated += 1
                print(f"[migrate] Updated row {row_id} for user {user_id}")
            except Exception as e:
                errors += 1
                print(f"[migrate] Error updating row {row_id}: {e}", file=sys.stderr)

        if len(rows) < limit:
            break
        offset += limit

    print(f"[migrate] Done. Updated={updated}, Skipped={skipped}, Errors={errors}")


if __name__ == "__main__":
    main()
