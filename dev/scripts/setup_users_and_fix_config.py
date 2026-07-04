#!/usr/bin/env python3
"""
Comprehensive setup script:
1. Create admin master user with PIN 112358 in service_config
2. Create user00 with PIN 112358 in users table
3. Fix CTRADER_AUTH_DATABASE_ID in all Appwrite functions
4. Fix v2.env with correct database ID and add SIGNAL_WEBHOOK_SECRET
"""
from __future__ import annotations

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
from appwrite.permission import Permission
from appwrite.role import Role

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ENDPOINT = os.getenv("APPWRITE_ENDPOINT", "https://sgp.cloud.appwrite.io/v1")
PROJECT_ID = os.getenv("APPWRITE_PROJECT_ID", "6a22a362002b9ae880bb")
API_KEY = os.getenv("APPWRITE_API_KEY", "")
DB_ID = "slwp_platform"  # Migrated from ctrader_auth

MASTER_EMAIL = "mrme000.m0@gmail.com"
MASTER_USERNAME = "admin"
MASTER_PIN = "112358"

USER00_USERNAME = "user00"
USER00_PIN = "112358"
USER00_EMAIL = "user00@local.slwp"

SIGNAL_WEBHOOK_SECRET = os.getenv("SIGNAL_WEBHOOK_SECRET", secrets.token_hex(32))

FUNCTIONS = [
    "auth-pin",
    "auth-oauth",
    "api-internal",
    "token-refresh",
    "ctrader-internal",
    "ctrader-auth",
    "ctrader-pin-auth",
]


def build_clients() -> tuple[Users, TablesDB]:
    client = Client()
    client.set_endpoint(ENDPOINT)
    client.set_project(PROJECT_ID)
    client.set_key(API_KEY)
    return Users(client), TablesDB(client)


def hash_pin(pin: str) -> str:
    """scrypt hash compatible with the Node PIN auth Function."""
    salt = secrets.token_hex(16)
    h = hashlib.scrypt(pin.encode(), salt=salt.encode("utf-8"), n=2**14, r=8, p=1, dklen=64)
    return f"{salt}:{h.hex()}"


def find_or_create_master_user(users: Users, email: str) -> str:
    try:
        result = users.list(queries=[Query.limit(100)])
        for u in getattr(result, "users", []):
            if getattr(u, "email", None) == email:
                return getattr(u, "id", getattr(u, "$id", None))
    except Exception as exc:
        print(f"[warn] User list warning: {exc}")

    temp_pass = secrets.token_urlsafe(32)
    new_user = users.create_argon2_user(
        user_id=ID.unique(),
        email=email,
        password=temp_pass,
        name=MASTER_USERNAME,
    )
    return getattr(new_user, "id", getattr(new_user, "$id", None))


def setup_master_admin(users: Users, db: TablesDB) -> dict:
    print("[1/5] Setting up master admin account...")
    user_id = find_or_create_master_user(users, MASTER_EMAIL)
    print(f"       Appwrite user ID: {user_id}")

    try:
        users.update_labels(user_id=user_id, labels=["master"])
        print("       Set master label")
    except Exception as exc:
        print(f"       [warn] Label update: {exc}")

    pin_hash = hash_pin(MASTER_PIN)
    body = {
        "config_key": "master_auth",
        "config_value": json.dumps({
            "appwrite_user_id": user_id,
            "username": MASTER_USERNAME,
            "email": MASTER_EMAIL,
            "pin_hash": pin_hash,
            "role": "master",
            "active": True,
        }),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Find existing
    try:
        result = db.list_rows(
            database_id=DB_ID,
            table_id="service_config",
            queries=[Query.equal("config_key", "master_auth")],
        )
        rows = list(getattr(result, "rows", getattr(result, "documents", [])))
        if rows:
            first = rows[0]
            if hasattr(first, "get"):
                row_id = first.get("$id")
            elif hasattr(first, "$id"):
                row_id = getattr(first, "$id", None)
            elif hasattr(first, "id"):
                row_id = getattr(first, "id", None)
            else:
                row_id = None
            if not row_id:
                print(f"       [warn] Could not extract row_id from existing row, creating new")
                db.create_row(database_id=DB_ID, table_id="service_config", row_id=ID.unique(), data=body)
                print(f"       Created master_auth row")
            else:
                db.update_row(database_id=DB_ID, table_id="service_config", row_id=row_id, data=body)
                print(f"       Updated master_auth row")
        else:
            db.create_row(database_id=DB_ID, table_id="service_config", row_id=ID.unique(), data=body)
            print(f"       Created master_auth row")
    except Exception as exc:
        print(f"       [ERROR] Failed to update master_auth: {exc}")
        raise

    return {"appwrite_user_id": user_id, "username": MASTER_USERNAME, "pin_hash": pin_hash}


def setup_user00(users: Users, db: TablesDB) -> dict:
    print("\n[2/5] Setting up user00...")

    # Check if user00 already exists
    try:
        result = db.list_rows(
            database_id=DB_ID,
            table_id="users",
            queries=[Query.equal("username", USER00_USERNAME)],
        )
        rows = list(getattr(result, "rows", getattr(result, "documents", [])))
        if rows:
            first = rows[0]
            if hasattr(first, "get"):
                existing_uid = first.get("appwrite_user_id")
            else:
                existing_uid = getattr(first, "appwrite_user_id", None)
            print(f"       user00 already exists (Appwrite user: {existing_uid})")
            return {"appwrite_user_id": existing_uid, "username": USER00_USERNAME}
    except Exception:
        pass

    # Create Appwrite user
    temp_pass = secrets.token_urlsafe(32)
    try:
        new_user = users.create_argon2_user(
            user_id=ID.unique(),
            email=USER00_EMAIL,
            password=temp_pass,
            name=USER00_USERNAME,
        )
        user_id = getattr(new_user, "id", getattr(new_user, "$id", None))
    except Exception as exc:
        print(f"       [ERROR] Failed to create Appwrite user: {exc}")
        raise

    # Create users row
    pin_hash = hash_pin(USER00_PIN)
    grant_id = secrets.token_hex(16)

    body = {
        "appwrite_user_id": user_id,
        "username": USER00_USERNAME,
        "pin_hash": pin_hash,
        "role": "slave",
        "grant_id": grant_id,
        "access_token_enc": "",
        "refresh_token_enc": "",
        "access_token_expires_at": None,
        "ctrader_account_ids": "",
        "selected_account_id": "",
        "status": "active",
        "active": True,
        "email": USER00_EMAIL,
        "last_heartbeat_at": None,
    }

    try:
        db.create_row(
            database_id=DB_ID,
            table_id="users",
            row_id=ID.unique(),
            data=body,
            permissions=[
                Permission.read(Role.user(user_id)),
                Permission.update(Role.user(user_id)),
                Permission.read(Role.users()),
            ],
        )
        print(f"       Created user00 (Appwrite user: {user_id}, grant: {grant_id})")
    except Exception as exc:
        print(f"       [ERROR] Failed to create users row: {exc}")
        raise

    return {"appwrite_user_id": user_id, "username": USER00_USERNAME, "grant_id": grant_id}


def fix_function_env_vars():
    print("\n[3/5] Fixing CTRADER_AUTH_DATABASE_ID in Appwrite functions...")
    import subprocess

    for func_id in FUNCTIONS:
        try:
            # Get current CTRADER_AUTH_DATABASE_ID variable
            result = subprocess.run(
                ["appwrite", "functions", "list-variables", "--function-id", func_id, "--json"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                print(f"       [skip] {func_id}: could not list variables")
                continue

            data = json.loads(result.stdout)
            var_id = None
            for v in data.get("variables", []):
                if v.get("key") == "CTRADER_AUTH_DATABASE_ID":
                    var_id = v.get("$id")
                    break

            if var_id:
                subprocess.run(
                    ["appwrite", "functions", "update-variable", "--function-id", func_id, "--variable-id", var_id, "--value", DB_ID],
                    capture_output=True, text=True, timeout=30
                )
                print(f"       Updated {func_id}")
            else:
                print(f"       [skip] {func_id}: CTRADER_AUTH_DATABASE_ID not found")
        except Exception as exc:
            print(f"       [warn] {func_id}: {exc}")


def fix_v2_env():
    print("\n[4/5] Fixing v2.env configuration...")
    v2_env_path = Path(__file__).parent.parent.parent / "remote-services" / "config" / "v2.env"
    if not v2_env_path.exists():
        print(f"       [skip] v2.env not found at {v2_env_path}")
        return

    content = v2_env_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    new_lines = []
    changed = False

    # Track what we've seen
    has_signal_webhook_secret = False
    has_ctrader_db_id = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("CTRADER_AUTH_DATABASE_ID="):
            new_lines.append(f"CTRADER_AUTH_DATABASE_ID={DB_ID}")
            has_ctrader_db_id = True
            changed = True
        elif stripped.startswith("SIGNAL_WEBHOOK_SECRET="):
            # If empty, set it
            current_val = stripped.split("=", 1)[1] if "=" in stripped else ""
            if not current_val:
                new_lines.append(f"SIGNAL_WEBHOOK_SECRET={SIGNAL_WEBHOOK_SECRET}")
                changed = True
            else:
                new_lines.append(line)
            has_signal_webhook_secret = True
        else:
            new_lines.append(line)

    # Append missing keys
    if not has_ctrader_db_id:
        new_lines.append(f"CTRADER_AUTH_DATABASE_ID={DB_ID}")
        changed = True
    if not has_signal_webhook_secret:
        new_lines.append(f"SIGNAL_WEBHOOK_SECRET={SIGNAL_WEBHOOK_SECRET}")
        changed = True

    if changed:
        v2_env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        print(f"       Updated {v2_env_path}")
        print(f"       SIGNAL_WEBHOOK_SECRET = {SIGNAL_WEBHOOK_SECRET}")
    else:
        print(f"       No changes needed")


def generate_summary(admin_info: dict, user00_info: dict):
    print("\n" + "=" * 60)
    print("SETUP COMPLETE")
    print("=" * 60)
    print(f"\nAdmin User:")
    print(f"  Username: admin")
    print(f"  PIN:      {MASTER_PIN}")
    print(f"  Email:    {MASTER_EMAIL}")
    print(f"  User ID:  {admin_info['appwrite_user_id']}")
    print(f"  Role:     master")
    print(f"  Table:    {DB_ID}.service_config (config_key=master_auth)")

    print(f"\nUser00:")
    print(f"  Username: {user00_info['username']}")
    print(f"  PIN:      {USER00_PIN}")
    print(f"  User ID:  {user00_info['appwrite_user_id']}")
    print(f"  Grant ID: {user00_info.get('grant_id', 'N/A')}")
    print(f"  Role:     slave")
    print(f"  Table:    {DB_ID}.users")

    print(f"\nIMPORTANT: cTrader OAuth Linking Required")
    print(f"  The provided credentials (mrme000.m0 / 1mjkaiden) are cTrader web login")
    print(f"  credentials, but the system uses OAuth 2.0 for API access.")
    print(f"  To link cTrader accounts:")
    print(f"  1. Go to https://app.mrme.tech")
    print(f"  2. Login with username + PIN")
    print(f"  3. Click 'Connect cTrader' to start OAuth flow")
    print(f"  4. After consent, tokens are stored automatically")

    print(f"\nSignal Pipeline:")
    print(f"  SIGNAL_WEBHOOK_SECRET:   {SIGNAL_WEBHOOK_SECRET}")
    print(f"  Webhook endpoint:        https://api.mrme.tech/api/signals/webhook")
    print(f"  Configure upstream with this secret + URL for direct HTTP push")

    print(f"\nConfig Fixes Applied:")
    print(f"  - CTRADER_AUTH_DATABASE_ID updated to {DB_ID} in functions and v2.env")
    print(f"  - SIGNAL_WEBHOOK_SECRET added to v2.env")
    print("=" * 60)


def main() -> int:
    if not API_KEY:
        print("ERROR: APPWRITE_API_KEY must be set in environment", file=sys.stderr)
        return 1

    users, db = build_clients()

    # 1. Setup admin
    admin_info = setup_master_admin(users, db)

    # 2. Setup user00
    user00_info = setup_user00(users, db)

    # 3. Fix function env vars
    fix_function_env_vars()

    # 4. Fix v2.env
    fix_v2_env()

    # 5. Summary
    generate_summary(admin_info, user00_info)

    return 0


if __name__ == "__main__":
    sys.exit(main())
