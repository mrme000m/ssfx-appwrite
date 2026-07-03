#!/usr/bin/env python3
"""dev/scripts/init/pplx-agent.py — Persist PPLX Agent configuration to Appwrite TablesDB."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from appwrite.client import Client
from appwrite.id import ID
from appwrite.query import Query
from appwrite.services.tables_db import TablesDB

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pplx-agent"))

from _env import load_env  # noqa: E402
from pplx_agent.config import PplxAgentSettings  # noqa: E402

load_env()

DB_ID = "ctrader_auth"
SERVICE_CONFIG_KEY = "pplx_agent"


def read_config() -> dict[str, object]:
    here = Path(__file__).parent
    config_file = here / "config.yml"
    if not config_file.exists():
        print(f"Error: {config_file} not found.", file=sys.stderr)
        sys.exit(1)
    with open(config_file, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("pplx_agent", {})


def build_tables_db() -> TablesDB:
    client = Client()
    client.set_endpoint(os.environ["APPWRITE_ENDPOINT"])
    client.set_project(os.environ["APPWRITE_PROJECT_ID"])
    client.set_key(os.environ["APPWRITE_API_KEY"])
    return TablesDB(client)


def _row_id(row) -> str | None:
    if hasattr(row, "get"):
        return row.get("$id")
    return getattr(row, "$id", getattr(row, "id", None))


def find_existing(db: TablesDB) -> dict | None:
    try:
        result = db.list_rows(
            database_id=DB_ID,
            table_id="service_config",
            queries=[Query.equal("config_key", SERVICE_CONFIG_KEY)],
        )
        rows = getattr(result, "documents", getattr(result, "rows", []))
        if rows:
            return rows[0]
    except Exception as exc:
        print(f"[init] Config lookup warning: {exc}")
    return None


def main() -> int:
    pplx = read_config()
    if not pplx:
        print("pplx_agent section missing in dev/scripts/init/config.yml", file=sys.stderr)
        return 1

    print("[init] Persisting PPLX Agent config to Appwrite TablesDB...")

    # Map YAML config keys to the env aliases used by PplxAgentSettings.
    key_map = {
        "mode": "PPLX_MODE",
        "model": "PPLX_MODEL",
        "thinking": "PPLX_THINKING",
        "gold_market_space_uuid": "GOLD_MARKET_SPACE_UUID",
        "gold_market_space_name": "GOLD_MARKET_SPACE_NAME",
        "tv_user": "TV_USER",
        "tv_session": "TV_SESSION",
        "tv_signature": "TV_SIGNATURE",
        "symbol": "ANALYSIS_SYMBOL",
        "timeframes": "ANALYSIS_TIMEFRAMES",
        "data_service_base_url": "DATA_SERVICE_BASE_URL",
        "data_service_api_key": "DATA_SERVICE_API_KEY",
        "agent_harness_url": "AGENT_HARNESS_URL",
        "api_host": "API_HOST",
        "api_port": "API_PORT",
        "report_output_dir": "REPORT_OUTPUT_DIR",
        "cache_dir": "CACHE_DIR",
        "log_dir": "LOG_DIR",
        "log_level": "LOG_LEVEL",
        "report_retention_days": "REPORT_RETENTION_DAYS",
        "enable_macro": "PPLX_ENABLE_MACRO",
        "enable_technical": "PPLX_ENABLE_TECHNICAL",
        "enable_fundamental": "PPLX_ENABLE_FUNDAMENTAL",
        "enable_space_sync": "PPLX_ENABLE_SPACE_SYNC",
    }

    flat_env: dict[str, str] = {}
    for key, value in pplx.items():
        if key not in key_map:
            continue
        alias = key_map[key]
        if isinstance(value, bool):
            flat_env[alias] = str(value).lower()
        elif isinstance(value, list):
            flat_env[alias] = ",".join(str(v) for v in value)
        else:
            flat_env[alias] = str(value)

    settings = PplxAgentSettings(**flat_env)
    payload = {
        "config_key": SERVICE_CONFIG_KEY,
        "config_value": json.dumps(settings.to_appwrite_payload()),
        "description": "PPLX Agent configuration (Perplexity + TradingView gold research)",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        db = build_tables_db()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    existing = find_existing(db)
    if not existing:
        db.create_row(
            database_id=DB_ID,
            table_id="service_config",
            row_id=ID.unique(),
            data=payload,
        )
        print(f"[init] Created service_config row with key '{SERVICE_CONFIG_KEY}'")
    else:
        row_id = _row_id(existing)
        db.update_row(
            database_id=DB_ID,
            table_id="service_config",
            row_id=row_id,
            data=payload,
        )
        print(f"[init] Updated service_config row '{row_id}'")

    print(f"[init] Symbol: {settings.analysis_symbol}")
    print(f"[init] Space name: {settings.gold_market_space_name}")
    print(f"[init] Perplexity mode: {settings.pplx_mode} / model: {settings.pplx_model}")
    print("\nRun './dev.sh pplx-agent setup' to create the Perplexity Space.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
