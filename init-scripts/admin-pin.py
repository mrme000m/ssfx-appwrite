#!/usr/bin/env python3
"""init-scripts/admin-pin.py — Idempotently create master admin user + PIN row."""

import os
import sys
import getpass
import hashlib
import secrets
from pathlib import Path

from appwrite.client import Client
from appwrite.services.users import Users
from appwrite.services.tables_db import TablesDB
from appwrite.id import ID
from appwrite.query import Query
from appwrite.permission import Permission
from appwrite.role import Role


def load_env():
    """Load .env from project root."""
    root = Path(__file__).parent.parent
    env_file = root / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key, val)


def load_config():
    """Read master.email from config.yml."""
    here = Path(__file__).parent
    config_file = here / "config.yml"
    if not config_file.exists():
        print(f"Error: {config_file} not found. Copy config.example.yml to config.yml.", file=sys.stderr)
        sys.exit(1)

    import yaml
    with open(config_file) as f:
        cfg = yaml.safe_load(f)

    email = cfg.get("master", {}).get("email", "")
    if not email:
        print("Error: master.email missing in config.yml", file=sys.stderr)
        sys.exit(1)
    return email


def hash_pin(pin: str) -> str:
    """scrypt hash (same format as the Node PIN auth Function)."""
    salt = secrets.token_hex(16)
    h = hashlib.scrypt(pin.encode(), salt=salt.encode(), n=2**14, r=8, p=1, dklen=64)
    return f"{salt}:{h.hex()}"


def main():
    load_env()

    master_email = load_config()
    print(f"[init] Setting up master admin account...")
    print(f"[init] Email: {master_email}")

    # Prompt for PIN
    if sys.stdin.isatty():
        pin = getpass.getpass("Enter master PIN (4-6 digits): ")
    else:
        pin = input("Enter master PIN (4-6 digits): ")
    if not pin.isdigit() or not (4 <= len(pin) <= 6):
        print("Error: PIN must be 4-6 digits.", file=sys.stderr)
        sys.exit(1)

    # Connect to Appwrite
    client = Client()
    client.set_endpoint(os.environ["APPWRITE_ENDPOINT"])
    client.set_project(os.environ["APPWRITE_PROJECT_ID"])
    client.set_key(os.environ["APPWRITE_API_KEY"])

    users = Users(client)
    db = TablesDB(client)

    project_id = os.environ["APPWRITE_PROJECT_ID"]
    db_id = "ctrader_auth"

    # Find or create user
    user_id = None
    try:
        user_list = users.list(queries=[Query.limit(100)])
        for u in user_list.users:
            if u.email == master_email:
                user_id = u.id
                print(f"[init] Found existing user {user_id}")
                break
    except Exception as e:
        print(f"[init] User list warning: {e}")

    if not user_id:
        print("[init] Creating Appwrite user for master...")
        temp_pass = secrets.token_urlsafe(32)
        try:
            new_user = users.create_argon2_user(
                user_id=ID.unique(),
                email=master_email,
                password=temp_pass,
                name="admin",
            )
            user_id = new_user["$id"]
            print(f"[init] Created user {user_id}")
        except Exception as e:
            print(f"Error: Failed to create user: {e}", file=sys.stderr)
            sys.exit(1)

    # Set master label
    print("[init] Setting master label...")
    try:
        users.update_labels(user_id=user_id, labels=["master"])
    except Exception as e:
        print(f"[init] Label update warning: {e}")

    # Hash PIN
    pin_hash = hash_pin(pin)
    master_row_id = f"master_{user_id}"

    # Upsert slave_accounts row
    print("[init] Upserting slave_accounts master row...")
    existing = None
    try:
        rows = db.list_rows(db_id, "slave_accounts", [
            Query.equal("appwrite_user_id", user_id),
        ])
        if rows.get("documents"):
            existing = rows["documents"][0]
    except Exception as e:
        print(f"[init] Row lookup warning: {e}")

    if not existing:
        print("[init] Creating master row...")
        try:
            db.create_row(
                db_id,
                "slave_accounts",
                master_row_id,
                {
                    "appwrite_user_id": user_id,
                    "username": "admin",
                    "pin_hash": pin_hash,
                    "role": "master",
                    "grant_id": "",
                    "status": "active",
                    "active": True,
                    "email": master_email,
                },
                permissions=[
                    Permission.read(Role.user(user_id)),
                    Permission.update(Role.user(user_id)),
                ],
            )
            print(f"[init] Created master row {master_row_id}")
        except Exception as e:
            print(f"Error: Row creation failed: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("[init] Updating master PIN...")
        try:
            db.update_row(
                db_id,
                "slave_accounts",
                existing["$id"],
                {"pin_hash": pin_hash, "active": True},
            )
            print(f"[init] Updated master PIN on row {existing['$id']}")
        except Exception as e:
            print(f"Error: Row update failed: {e}", file=sys.stderr)
            sys.exit(1)

    print()
    print("[init] Master admin ready.")
    print(f"       appwrite_user_id: {user_id}")
    print(f"       username:         admin")
    print(f"       email:            {master_email}")
    print()
    print("To rotate the PIN, simply rerun this script.")


if __name__ == "__main__":
    main()
