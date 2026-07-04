#!/usr/bin/env python3
"""dev/scripts/reset_admin.py — Wipe auth/user state and recreate the master admin.

This is a destructive dev helper. It removes all Appwrite users and auth-layer
rows, then recreates the master admin account in `service_config.master_auth`.

Usage:
    ./dev.sh reset-admin --email mrme000m0@gmail.com --pin 112358 --force
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from appwrite.client import Client
from appwrite.id import ID
from appwrite.query import Query
from appwrite.services.tables_db import TablesDB
from appwrite.services.users import Users

# Load project .env so APPWRITE_* vars are available.
here = Path(__file__).resolve().parent
root = here.parent.parent
env_file = root / ".env"
if env_file.exists():
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key, val)

DB_ID = "slwp_platform"
MASTER_AUTH_KEY = "master_auth"
MASTER_USERNAME = "admin"
DEFAULT_EMAIL = "mrme000m0@gmail.com"

AUTH_TABLES = ["users", "trade_settings", "ephemeral_tokens", "grant_locks"]
RUNTIME_TABLES = [
    "accounts",
    "account_state_history",
    "trading_events",
    "signal_broadcasts",
    "signal_slaves",
    "ssfx_executions",
]


def hash_pin(pin: str) -> str:
    """scrypt hash compatible with the Node PIN auth Function."""
    salt = secrets.token_hex(16)
    h = hashlib.scrypt(pin.encode(), salt=salt.encode("utf-8"), n=2**14, r=8, p=1, dklen=64)
    return f"{salt}:{h.hex()}"


def build_clients() -> tuple[Users, TablesDB]:
    for key in ("APPWRITE_ENDPOINT", "APPWRITE_PROJECT_ID", "APPWRITE_API_KEY"):
        if not os.environ.get(key):
            raise RuntimeError(f"Missing {key} in environment")
    client = Client()
    client.set_endpoint(os.environ["APPWRITE_ENDPOINT"])
    client.set_project(os.environ["APPWRITE_PROJECT_ID"])
    client.set_key(os.environ["APPWRITE_API_KEY"])
    return Users(client), TablesDB(client)


def _row_id(row) -> str | None:
    if hasattr(row, "get"):
        return row.get("$id")
    return getattr(row, "$id", getattr(row, "id", None))


def delete_all_users(users: Users) -> int:
    deleted = 0
    while True:
        result = users.list(queries=[Query.limit(100)])
        user_list = getattr(result, "users", [])
        if not user_list:
            break
        for user in user_list:
            user_id = getattr(user, "id", getattr(user, "$id", None))
            email = getattr(user, "email", "unknown")
            try:
                users.delete(user_id=user_id)
                print(f"  deleted user {email} ({user_id})")
                deleted += 1
            except Exception as exc:
                print(f"  failed to delete user {email} ({user_id}): {exc}", file=sys.stderr)
    return deleted


def delete_all_rows(db: TablesDB, table_id: str) -> int:
    deleted = 0
    while True:
        try:
            result = db.list_rows(database_id=DB_ID, table_id=table_id, queries=[Query.limit(100)])
        except Exception as exc:
            print(f"  skipping table {table_id}: {exc}", file=sys.stderr)
            return deleted
        rows = getattr(result, "rows", getattr(result, "documents", []))
        if not rows:
            break
        for row in rows:
            row_id = _row_id(row)
            if not row_id:
                continue
            try:
                db.delete_row(database_id=DB_ID, table_id=table_id, row_id=row_id)
                deleted += 1
            except Exception as exc:
                print(f"  failed to delete row {row_id} in {table_id}: {exc}", file=sys.stderr)
    return deleted


def delete_master_auth(db: TablesDB) -> None:
    try:
        result = db.list_rows(
            database_id=DB_ID, table_id="service_config", queries=[Query.equal("config_key", MASTER_AUTH_KEY)]
        )
        rows = getattr(result, "rows", getattr(result, "documents", []))
        for row in rows:
            row_id = _row_id(row)
            if row_id:
                db.delete_row(database_id=DB_ID, table_id="service_config", row_id=row_id)
                print(f"  deleted master_auth row {row_id}")
    except Exception as exc:
        print(f"  failed to delete master_auth: {exc}", file=sys.stderr)


def create_admin(users: Users, db: TablesDB, email: str, pin: str) -> str:
    temp_pass = secrets.token_urlsafe(32)
    new_user = users.create_argon2_user(
        user_id=ID.unique(),
        email=email,
        password=temp_pass,
        name=MASTER_USERNAME,
    )
    user_id = getattr(new_user, "id", getattr(new_user, "$id", None))
    users.update_labels(user_id=user_id, labels=["master"])

    pin_hash = hash_pin(pin)
    body = {
        "config_key": MASTER_AUTH_KEY,
        "config_value": json.dumps(
            {
                "appwrite_user_id": user_id,
                "username": MASTER_USERNAME,
                "email": email,
                "pin_hash": pin_hash,
                "role": "master",
                "active": True,
            }
        ),
        "description": "Master admin authentication record",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    db.create_row(database_id=DB_ID, table_id="service_config", row_id=ID.unique(), data=body)
    return user_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset auth/user state and recreate the master admin.")
    parser.add_argument("--email", default=DEFAULT_EMAIL, help="Admin email address")
    parser.add_argument("--pin", help="Admin PIN (4-6 digits). Prompted if not provided.")
    parser.add_argument("--all", action="store_true", help="Also clear runtime/trading tables")
    parser.add_argument("--force", action="store_true", help="Skip interactive confirmation")
    args = parser.parse_args()

    if not args.pin:
        if sys.stdin.isatty():
            args.pin = getpass.getpass("Enter master PIN (4-6 digits): ")
        else:
            print("Error: --pin is required in non-interactive mode.", file=sys.stderr)
            return 1

    if not args.pin.isdigit() or not (4 <= len(args.pin) <= 6):
        print("Error: PIN must be 4-6 digits.", file=sys.stderr)
        return 1

    if not args.force:
        print("This will DELETE all Appwrite users and auth rows. This cannot be undone.")
        confirm = input("Type 'yes' to continue: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            return 1

    users, db = build_clients()

    print("[reset] Deleting Appwrite users...")
    deleted_users = delete_all_users(users)
    print(f"[reset] Deleted {deleted_users} user(s)")

    print("[reset] Clearing auth tables...")
    for table in AUTH_TABLES:
        count = delete_all_rows(db, table)
        print(f"[reset]   {table}: {count} row(s) deleted")

    if args.all:
        print("[reset] Clearing runtime/trading tables...")
        for table in RUNTIME_TABLES:
            count = delete_all_rows(db, table)
            print(f"[reset]   {table}: {count} row(s) deleted")

    print("[reset] Removing old master_auth config...")
    delete_master_auth(db)

    print("[reset] Creating master admin account...")
    user_id = create_admin(users, db, args.email, args.pin)

    print()
    print("[reset] Master admin recreated.")
    print(f"       appwrite_user_id: {user_id}")
    print(f"       username:         {MASTER_USERNAME}")
    print(f"       email:            {args.email}")
    print(f"       pin:              {'*' * len(args.pin)}")
    print()
    print("If login still fails immediately, wait 15 minutes for any warm function")
    print("lockout to clear, or redeploy the auth-pin function.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
