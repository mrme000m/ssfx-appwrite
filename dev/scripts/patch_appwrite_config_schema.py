#!/usr/bin/env python3
"""Idempotent patcher for appwrite.config.json schema gaps.

Adds:
- ssfx_presets table
- ssfx_risk_state table
- updated_at columns on ssfx_accounts and accounts
- config_json column on service_config
- longtext event_json on account_events
- Missing indexes on accounts, account_events, ssfx_accounts, ssfx_executions
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
CONFIG_PATH = PROJECT_ROOT / "appwrite.config.json"


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict[str, Any]) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
        f.write("\n")


def find_table(config: dict[str, Any], table_id: str) -> dict[str, Any] | None:
    for table in config.get("tables", []):
        if table.get("$id") == table_id:
            return table
    return None


def ensure_column(table: dict[str, Any], column: dict[str, Any]) -> bool:
    existing = {c.get("key") for c in table.get("columns", [])}
    key = column["key"]
    if key in existing:
        return False
    table.setdefault("columns", []).append(column)
    return True


def ensure_index(table: dict[str, Any], index: dict[str, Any]) -> bool:
    existing = {i.get("key") for i in table.get("indexes", [])}
    key = index["key"]
    if key in existing:
        return False
    index.setdefault("status", "available")
    index.setdefault("orders", [])
    table.setdefault("indexes", []).append(index)
    return True


def new_table(table_id: str, name: str, database_id: str, columns: list[dict], indexes: list[dict]) -> dict[str, Any]:
    return {
        "$id": table_id,
        "$permissions": [],
        "databaseId": database_id,
        "name": name,
        "enabled": True,
        "rowSecurity": False,
        "columns": columns,
        "indexes": indexes,
    }


def main() -> int:
    config = load_config()

    db_map = {}
    for table in config.get("tables", []):
        db_map[table.get("$id")] = table.get("databaseId", "ctrader_auth")

    # --- ssfx_accounts: add updated_at column and helper indexes ---
    ssfx = find_table(config, "ssfx_accounts")
    if ssfx:
        ensure_column(ssfx, {"key": "updated_at", "type": "datetime", "required": False, "array": False, "default": None, "format": ""})
        ensure_index(ssfx, {"key": "idx_owner_id", "type": "key", "columns": ["owner_id"]})
        ensure_index(ssfx, {"key": "idx_enabled", "type": "key", "columns": ["enabled"]})
        ensure_index(ssfx, {"key": "idx_host_type", "type": "key", "columns": ["host_type"]})
        ensure_index(ssfx, {"key": "idx_enabled_host_type", "type": "key", "columns": ["enabled", "host_type"]})
        print("[schema] ssfx_accounts patched")
    else:
        print("[schema] ssfx_accounts not found", file=sys.stderr)

    # --- accounts: add updated_at and indexes ---
    accounts = find_table(config, "accounts")
    if accounts:
        ensure_column(accounts, {"key": "updated_at", "type": "datetime", "required": False, "array": False, "default": None, "format": ""})
        ensure_index(accounts, {"key": "idx_grant_id", "type": "key", "columns": ["grant_id"]})
        ensure_index(accounts, {"key": "idx_ctid_trader_account", "type": "key", "columns": ["ctidTraderAccountId"]})
        ensure_index(accounts, {"key": "idx_grant_id_ctid", "type": "unique", "columns": ["grant_id", "ctidTraderAccountId"]})
        ensure_index(accounts, {"key": "idx_is_live", "type": "key", "columns": ["isLive"]})
        ensure_index(accounts, {"key": "idx_grant_selected", "type": "key", "columns": ["grant_id", "selected"]})
        print("[schema] accounts patched")
    else:
        print("[schema] accounts not found", file=sys.stderr)

    # --- account_events: add indexes and ensure event_json is longtext ---
    events = find_table(config, "account_events")
    if events:
        for col in events.get("columns", []):
            if col.get("key") == "event_json":
                if col.get("type") != "longtext":
                    col["type"] = "longtext"
                    print("[schema] account_events.event_json upgraded to longtext")
                break
        ensure_index(events, {"key": "idx_grant_id", "type": "key", "columns": ["grant_id"]})
        ensure_index(events, {"key": "idx_ctid_trader_account_id", "type": "key", "columns": ["ctid_trader_account_id"]})
        ensure_index(events, {"key": "idx_grant_ctid_event", "type": "key", "columns": ["grant_id", "ctid_trader_account_id", "event_type"]})
        ensure_index(events, {"key": "idx_event_type", "type": "key", "columns": ["event_type"]})
        ensure_index(events, {"key": "idx_received_at", "type": "key", "columns": ["received_at"]})
        ensure_index(events, {"key": "idx_timestamp_ms", "type": "key", "columns": ["timestamp_ms"]})
        print("[schema] account_events patched")
    else:
        print("[schema] account_events not found", file=sys.stderr)

    # --- ssfx_executions: add account_name index ---
    executions = find_table(config, "ssfx_executions")
    if executions:
        ensure_index(executions, {"key": "idx_account_name", "type": "key", "columns": ["account_name"]})
        print("[schema] ssfx_executions patched")
    else:
        print("[schema] ssfx_executions not found", file=sys.stderr)

    # --- service_config: add config_json column ---
    svc = find_table(config, "service_config")
    if svc:
        ensure_column(svc, {"key": "config_json", "type": "longtext", "required": False, "array": False, "default": None, "encrypt": False})
        print("[schema] service_config patched")
    else:
        print("[schema] service_config not found", file=sys.stderr)

    # --- Add ssfx_presets table ---
    if not find_table(config, "ssfx_presets"):
        presets = new_table(
            table_id="ssfx_presets",
            name="SSFX Presets",
            database_id="ctrader_auth",
            columns=[
                {"key": "name", "type": "varchar", "required": True, "array": False, "size": 64, "default": None, "encrypt": False},
                {"key": "description", "type": "text", "required": False, "array": False, "default": None, "encrypt": False},
                {"key": "config_json", "type": "longtext", "required": True, "array": False, "default": None, "encrypt": False},
                {"key": "owner_id", "type": "varchar", "required": False, "array": False, "size": 64, "default": None, "encrypt": False},
                {"key": "updated_at", "type": "datetime", "required": False, "array": False, "default": None, "format": ""},
            ],
            indexes=[{"key": "idx_name", "type": "unique", "columns": ["name"]}],
        )
        config.setdefault("tables", []).append(presets)
        print("[schema] ssfx_presets table added")

    # --- Add ssfx_risk_state table ---
    if not find_table(config, "ssfx_risk_state"):
        risk = new_table(
            table_id="ssfx_risk_state",
            name="SSFX Risk State",
            database_id="ctrader_auth",
            columns=[
                {"key": "account_name", "type": "varchar", "required": True, "array": False, "size": 64, "default": None, "encrypt": False},
                {"key": "date_str", "type": "varchar", "required": True, "array": False, "size": 16, "default": None, "encrypt": False},
                {"key": "daily_start_equity", "type": "double", "required": False, "array": False, "default": None, "encrypt": False},
                {"key": "daily_pnl", "type": "double", "required": False, "array": False, "default": None, "encrypt": False},
                {"key": "peak_equity", "type": "double", "required": False, "array": False, "default": None, "encrypt": False},
                {"key": "kill_switch_active", "type": "boolean", "required": False, "array": False, "default": None, "encrypt": False},
                {"key": "kill_switch_reason", "type": "varchar", "required": False, "array": False, "size": 64, "default": None, "encrypt": False},
                {"key": "state_json", "type": "text", "required": False, "array": False, "default": None, "encrypt": False},
                {"key": "updated_at", "type": "datetime", "required": False, "array": False, "default": None, "format": ""},
            ],
            indexes=[{"key": "idx_account_date", "type": "unique", "columns": ["account_name", "date_str"]}],
        )
        config.setdefault("tables", []).append(risk)
        print("[schema] ssfx_risk_state table added")

    save_config(config)
    print(f"[schema] Saved {CONFIG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
