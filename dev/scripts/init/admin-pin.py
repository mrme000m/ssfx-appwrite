#!/usr/bin/env python3
"""dev/scripts/init/admin-pin.py — Idempotently create master admin user + auth row."""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from appwrite.client import Client
from appwrite.id import ID
from appwrite.query import Query
from appwrite.services.tables_db import TablesDB
from appwrite.services.users import Users

from _env import load_env

DB_ID = "ctrader_auth"
MASTER_AUTH_KEY = "master_auth"
MASTER_USERNAME = "admin"


def load_config() -> str:
    """Read master.email from config.yml."""
    here = Path(__file__).parent
    config_file = here / "config.yml"
    if not config_file.exists():
        print(f"Error: {config_file} not found. Copy config.example.yml to config.yml.", file=sys.stderr)
        sys.exit(1)
    with open(config_file, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    email = cfg.get("master", {}).get("email", "")
    if not email:
        print("Error: master.email missing in config.yml", file=sys.stderr)
        sys.exit(1)
    return email


def hash_pin(pin: str) -> str:
    """scrypt hash compatible with the Node PIN auth Function.

    The Node function passes the salt as a hex string to crypto.scryptSync,
    so it hashes against the UTF-8 bytes of the hex string. Match that here.
    """
    salt = secrets.token_hex(16)
    h = hashlib.scrypt(pin.encode(), salt=salt.encode("utf-8"), n=2**14, r=8, p=1, dklen=64)
    return f"{salt}:{h.hex()}"


def build_clients() -> tuple[Users, TablesDB]:
    client = Client()
    client.set_endpoint(os.environ["APPWRITE_ENDPOINT"])
    client.set_project(os.environ["APPWRITE_PROJECT_ID"])
    client.set_key(os.environ["APPWRITE_API_KEY"])
    return Users(client), TablesDB(client)


def find_or_create_master_user(users: Users, email: str) -> str:
    """Return the Appwrite user ID for the master email, creating if necessary."""
    try:
        result = users.list(queries=[Query.limit(100)])
        for u in getattr(result, "users", []):
            if getattr(u, "email", None) == email:
                return getattr(u, "id", getattr(u, "$id", None))
    except Exception as exc:
        print(f"[init] User list warning: {exc}")

    temp_pass = secrets.token_urlsafe(32)
    new_user = users.create_argon2_user(
        user_id=ID.unique(),
        email=email,
        password=temp_pass,
        name=MASTER_USERNAME,
    )
    return getattr(new_user, "id", getattr(new_user, "$id", None))


def _row_id(row) -> str | None:
    if hasattr(row, "get"):
        return row.get("$id")
    return getattr(row, "$id", getattr(row, "id", None))


def find_existing_master(db: TablesDB) -> dict | None:
    try:
        result = db.list_rows(
            database_id=DB_ID,
            table_id="service_config",
            queries=[Query.equal("config_key", MASTER_AUTH_KEY)],
        )
        rows = getattr(result, "documents", getattr(result, "rows", []))
        if rows:
            return rows[0]
    except Exception as exc:
        print(f"[init] Master auth lookup warning: {exc}")
    return None


def main() -> int:
    load_env()

    master_email = load_config()
    print("[init] Setting up master admin account...")
    print(f"[init] Email: {master_email}")

    if sys.stdin.isatty():
        pin = getpass.getpass("Enter master PIN (4-6 digits): ")
    else:
        pin = input("Enter master PIN (4-6 digits): ")
    if not pin.isdigit() or not (4 <= len(pin) <= 6):
        print("Error: PIN must be 4-6 digits.", file=sys.stderr)
        sys.exit(1)

    users, db = build_clients()
    user_id = find_or_create_master_user(users, master_email)

    try:
        users.update_labels(user_id=user_id, labels=["master"])
        print("[init] Set master label")
    except Exception as exc:
        print(f"[init] Label update warning: {exc}")

    pin_hash = hash_pin(pin)
    body = {
        "config_key": MASTER_AUTH_KEY,
        "config_value": json.dumps({
            "appwrite_user_id": user_id,
            "username": MASTER_USERNAME,
            "email": master_email,
            "pin_hash": pin_hash,
            "role": "master",
            "active": True,
        }),
        "description": "Master admin authentication record",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    existing = find_existing_master(db)
    if not existing:
        db.create_row(
            database_id=DB_ID,
            table_id="service_config",
            row_id=ID.unique(),
            data=body,
        )
        print("[init] Created master_auth row in service_config")
    else:
        row_id = _row_id(existing)
        db.update_row(
            database_id=DB_ID,
            table_id="service_config",
            row_id=row_id,
            data=body,
        )
        print(f"[init] Updated master_auth row '{row_id}'")

    print()
    print("[init] Master admin ready.")
    print(f"       appwrite_user_id: {user_id}")
    print(f"       username:         {MASTER_USERNAME}")
    print(f"       email:            {master_email}")
    print()
    print("To rotate the PIN, simply rerun this script.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
