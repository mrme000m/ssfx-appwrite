"""Appwrite TablesDB configuration sync helpers.

Used by both the dev scripts (``scripts/config_*.py``) and the running
Data Service to keep ``config.yml`` / ``.env`` in sync with the canonical
``service_config`` row in Appwrite.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from .config import BASE_DIR, DEFAULT_ENV_PATH, DEFAULT_YAML_PATH, get_settings

logger = logging.getLogger(__name__)

ENV_MAP: dict[str, tuple[str, ...]] = {
    "database": (
        ("backend", "MARKET_DATA_DB_BACKEND"),
        ("influxdb_host", "MARKET_DATA_INFLUXDB_HOST"),
        ("influxdb_token", "MARKET_DATA_INFLUXDB_TOKEN"),
        ("influxdb_org", "MARKET_DATA_INFLUXDB_ORG"),
        ("influxdb_database", "MARKET_DATA_INFLUXDB_DATABASE"),
        ("influxdb_retention_days", "MARKET_DATA_INFLUXDB_RETENTION_DAYS"),
        ("influxdb_sidecar_path", "MARKET_DATA_INFLUXDB_SIDECAR_PATH"),
        ("mongodb_uri", "MARKET_DATA_MONGODB_URI"),
        ("mongodb_database", "MARKET_DATA_MONGODB_DATABASE"),
        ("appwrite_endpoint", "MARKET_DATA_APPWRITE_ENDPOINT"),
        ("appwrite_project_id", "MARKET_DATA_APPWRITE_PROJECT_ID"),
        ("appwrite_database_id", "MARKET_DATA_APPWRITE_DATABASE_ID"),
        ("appwrite_api_key", "MARKET_DATA_APPWRITE_API_KEY"),
    ),
    "ctrader": (
        ("broker_url", "CTRADER_AUTH_BROKER_URL"),
        ("grant_id", "CTRADER_AUTH_GRANT_ID"),
        ("client_id", "CTRADER_CLIENT_ID"),
        ("client_secret", "CTRADER_CLIENT_SECRET"),
        ("account_id", "CTRADER_ACCOUNT_ID"),
        ("host_type", "CTRADER_HOST_TYPE"),
    ),
    "service": (
        ("name", "MARKET_DATA_SERVER_NAME"),
        ("host", "MARKET_DATA_SERVER_HOST"),
        ("port", "MARKET_DATA_SERVER_PORT"),
        ("ds_control_port", "DS_CONTROL_PORT"),
        ("api_port", "MARKET_DATA_API_PORT"),
        ("log_level", "MARKET_DATA_LOG_LEVEL"),
    ),
}


def _require_appwrite() -> Any:
    try:
        from appwrite.client import Client
        from appwrite.services.tables_db import TablesDB
        return Client, TablesDB
    except ImportError as exc:
        raise RuntimeError("appwrite python SDK not installed. Run: pip install appwrite") from exc


def get_appwrite_ids() -> tuple[str, str, str]:
    """Return (database_id, table_id, row_id) for the service config row."""
    return (
        os.getenv("APPWRITE_DATABASE_ID", "market_data"),
        os.getenv("APPWRITE_CONFIG_TABLE_ID", "service_config"),
        os.getenv("APPWRITE_CONFIG_ROW_ID", "service_config"),
    )


def _get_client() -> Any:
    """Build an Appwrite client from environment / settings."""
    Client, TablesDB = _require_appwrite()
    settings = get_settings()

    endpoint = os.getenv("APPWRITE_ENDPOINT", settings.appwrite_endpoint or "https://sgp.cloud.appwrite.io/v1")
    project_id = os.getenv("APPWRITE_PROJECT_ID", settings.appwrite_project_id)
    api_key = os.getenv("APPWRITE_API_KEY", settings.appwrite_api_key)

    if not project_id:
        raise RuntimeError("APPWRITE_PROJECT_ID / MARKET_DATA_APPWRITE_PROJECT_ID is not configured")
    if not api_key:
        raise RuntimeError("APPWRITE_API_KEY / MARKET_DATA_APPWRITE_API_KEY is not configured")

    client = Client()
    client.set_endpoint(endpoint).set_project(project_id).set_key(api_key)
    return client


def _get_tables() -> Any:
    _, TablesDB = _require_appwrite()
    return TablesDB(_get_client())


def _row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "data") and isinstance(row.data, dict):
        return dict(row.data)
    if hasattr(row, "model_dump"):
        return dict(row.model_dump())
    return dict(row)


def fetch_service_config() -> tuple[dict[str, dict[str, Any]], str | None]:
    """Fetch the service config row from Appwrite TablesDB.

    The canonical Appwrite row stores the nested config under ``config_json``.
    Returns a tuple of (nested config dict, updated_at string).
    """
    database_id, table_id, row_id = get_appwrite_ids()
    tables = _get_tables()
    row = tables.get_row(database_id, table_id, row_id)
    row_dict = _row_to_dict(row)

    raw_config_json = row_dict.get("config_json", "{}")
    try:
        loaded = json.loads(raw_config_json) if isinstance(raw_config_json, str) else dict(raw_config_json)
    except Exception:
        loaded = {}

    # New canonical format: {"database": {...}, "ctrader": {...}, "service": {...}}
    if isinstance(loaded, dict) and any(k in loaded for k in ("database", "ctrader", "service")):
        config: dict[str, dict[str, Any]] = {}
        for section in ("database", "ctrader", "service"):
            value = loaded.get(section, {})
            config[section] = value if isinstance(value, dict) else {}
        return config, row_dict.get("updated_at")

    # Legacy flat runtime config stored in config_json: migrate known keys into nested sections.
    return _migrate_legacy_config(loaded), row_dict.get("updated_at")


def _migrate_legacy_config(legacy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Convert a legacy flat runtime config into the nested section format."""
    config: dict[str, dict[str, Any]] = {"database": {}, "ctrader": {}, "service": {}}
    # Database keys
    for key in (
        "backend",
        "influxdb_host",
        "influxdb_token",
        "influxdb_org",
        "influxdb_database",
        "influxdb_retention_days",
        "influxdb_write_batch_size",
        "influxdb_max_write_bytes_per_sec",
        "influxdb_write_burst_bytes",
        "influxdb_max_read_bytes_per_sec",
        "influxdb_sidecar_path",
        "mongodb_uri",
        "mongodb_database",
        "appwrite_endpoint",
        "appwrite_project_id",
        "appwrite_database_id",
        "appwrite_api_key",
    ):
        if key in legacy:
            config["database"][key] = legacy[key]
    # cTrader keys
    for key in (
        "broker_url",
        "grant_id",
        "client_id",
        "client_secret",
        "account_id",
        "host_type",
    ):
        if key in legacy:
            config["ctrader"][key] = legacy[key]
    # Service / runtime keys (everything else)
    service_keys = {"name", "host", "port", "ds_control_port", "api_port", "log_level"}
    for key, value in legacy.items():
        if key in service_keys:
            config["service"][key] = value
        elif key not in config["database"] and key not in config["ctrader"]:
            config["service"][key] = value
    return config


def _to_yaml(config: dict[str, dict[str, Any]]) -> str:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML not installed") from exc
    return yaml.dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _update_env_file(key: str, value: str | int | float | bool) -> None:
    """Set or append a key in the dataservice ``.env`` file."""
    env_path = DEFAULT_ENV_PATH
    value_str = str(value)
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    updated = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value_str}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value_str}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_env_from_config(config: dict[str, dict[str, Any]]) -> None:
    """Persist Appwrite config values to ``.env`` so Pydantic Settings picks them up."""
    for section, mapping in ENV_MAP.items():
        section_values = config.get(section, {})
        for field_key, env_key in mapping:
            if field_key not in section_values:
                continue
            value = section_values[field_key]
            if value is None or value == "":
                # Do not clobber existing values with empty strings.
                continue
            _update_env_file(env_key, value)


def write_config_yml(config: dict[str, dict[str, Any]]) -> None:
    """Persist the nested config to ``dataservice-config.yml``."""
    config_yml = DEFAULT_YAML_PATH
    config_yml.write_text(_to_yaml(config), encoding="utf-8")


def pull_config_from_appwrite() -> tuple[dict[str, dict[str, Any]], str | None]:
    """Pull Appwrite config into local ``config.yml`` and ``.env``."""
    config, updated_at = fetch_service_config()
    write_config_yml(config)
    write_env_from_config(config)
    return config, updated_at


def flatten_config(config: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Convert a nested config dict into the Appwrite row payload."""
    return {
        "config_json": json.dumps(config),
        "updated_at": datetime_now_iso(),
    }


def datetime_now_iso() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


def push_config_to_appwrite(config: dict[str, dict[str, Any]]) -> None:
    """Upsert the nested config dict to the Appwrite ``service_config`` row."""
    database_id, table_id, row_id = get_appwrite_ids()
    tables = _get_tables()
    payload = flatten_config(config)
    tables.upsert_row(database_id, table_id, row_id, payload)


def load_local_config_yml() -> dict[str, dict[str, Any]]:
    """Load ``dataservice-config.yml`` as a nested dict."""
    config_yml = DEFAULT_YAML_PATH
    if not config_yml.exists():
        return {}
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML not installed") from exc
    with config_yml.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
