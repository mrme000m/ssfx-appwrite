"""Runtime configuration and settings management.

Loads from (in precedence order):
  1. Explicit environment variables
  2. .env file (Pydantic Settings)
  3. YAML config at <workspace_root>/config.yml (this workspace's own config)
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULT_YAML_PATH = BASE_DIR / "config" / "dataservice-config.yml"
DEFAULT_ENV_PATH = BASE_DIR / "config" / "dataservice.env"


def _load_yaml_config(path: Path) -> dict[str, str]:
    """Load workspace YAML config and return as flat env-style dict."""
    try:
        import yaml
    except ImportError:
        return {}
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return {}
    # Map nested YAML keys to our Settings env aliases
    mapping: dict[str, str] = {}

    database = data.get("database", {})
    if database.get("backend"):
        mapping["MARKET_DATA_DB_BACKEND"] = str(database["backend"])
    if database.get("sqlite_path"):
        mapping["MARKET_DATA_SQLITE_PATH"] = str(database["sqlite_path"])
    if database.get("appwrite_endpoint"):
        mapping["MARKET_DATA_APPWRITE_ENDPOINT"] = str(database["appwrite_endpoint"])
    if database.get("appwrite_project_id"):
        mapping["MARKET_DATA_APPWRITE_PROJECT_ID"] = str(database["appwrite_project_id"])
    if database.get("appwrite_database_id"):
        mapping["MARKET_DATA_APPWRITE_DATABASE_ID"] = str(database["appwrite_database_id"])
    if database.get("appwrite_api_key"):
        mapping["MARKET_DATA_APPWRITE_API_KEY"] = str(database["appwrite_api_key"])
    if database.get("influxdb_host"):
        mapping["MARKET_DATA_INFLUXDB_HOST"] = str(database["influxdb_host"])
    if database.get("influxdb_token"):
        mapping["MARKET_DATA_INFLUXDB_TOKEN"] = str(database["influxdb_token"])
    if database.get("influxdb_org"):
        mapping["MARKET_DATA_INFLUXDB_ORG"] = str(database["influxdb_org"])
    if database.get("influxdb_database"):
        mapping["MARKET_DATA_INFLUXDB_DATABASE"] = str(database["influxdb_database"])
    if database.get("influxdb_retention_days") is not None:
        mapping["MARKET_DATA_INFLUXDB_RETENTION_DAYS"] = str(database["influxdb_retention_days"])
    if database.get("influxdb_write_batch_size") is not None:
        mapping["MARKET_DATA_INFLUXDB_WRITE_BATCH_SIZE"] = str(database["influxdb_write_batch_size"])
    if database.get("influxdb_max_write_bytes_per_sec") is not None:
        mapping["MARKET_DATA_INFLUXDB_MAX_WRITE_BYTES_PER_SEC"] = str(database["influxdb_max_write_bytes_per_sec"])
    if database.get("influxdb_write_burst_bytes") is not None:
        mapping["MARKET_DATA_INFLUXDB_WRITE_BURST_BYTES"] = str(database["influxdb_write_burst_bytes"])
    if database.get("influxdb_max_read_bytes_per_sec") is not None:
        mapping["MARKET_DATA_INFLUXDB_MAX_READ_BYTES_PER_SEC"] = str(database["influxdb_max_read_bytes_per_sec"])
    if database.get("influxdb_sidecar_path"):
        mapping["MARKET_DATA_INFLUXDB_SIDECAR_PATH"] = str(database["influxdb_sidecar_path"])

    ctrader = data.get("ctrader", {})
    if ctrader.get("broker_url"):
        mapping["CTRADER_AUTH_BROKER_URL"] = str(ctrader["broker_url"])
    if ctrader.get("grant_id"):
        mapping["CTRADER_AUTH_GRANT_ID"] = str(ctrader["grant_id"])
    if ctrader.get("internal_api_key"):
        mapping["INTERNAL_API_KEY"] = str(ctrader["internal_api_key"])
    if ctrader.get("use_appwrite_auth") is not None:
        mapping["CTRADER_USE_APPWRITE_AUTH"] = str(ctrader["use_appwrite_auth"])
    if ctrader.get("appwrite_username"):
        mapping["CTRADER_APPWRITE_USERNAME"] = str(ctrader["appwrite_username"])
    if ctrader.get("auth_database_id"):
        mapping["CTRADER_AUTH_DATABASE_ID"] = str(ctrader["auth_database_id"])
    if ctrader.get("slave_accounts_table"):
        mapping["SLAVE_ACCOUNTS_TABLE"] = str(ctrader["slave_accounts_table"])
    if ctrader.get("account_events_table"):
        mapping["ACCOUNT_EVENTS_TABLE"] = str(ctrader["account_events_table"])
    if ctrader.get("client_id"):
        mapping["CTRADER_CLIENT_ID"] = str(ctrader["client_id"])
    if ctrader.get("client_secret"):
        mapping["CTRADER_CLIENT_SECRET"] = str(ctrader["client_secret"])
    if ctrader.get("account_id") is not None and ctrader.get("account_id") != "":
        mapping["CTRADER_ACCOUNT_ID"] = str(ctrader["account_id"])
    if ctrader.get("host_type"):
        mapping["CTRADER_HOST_TYPE"] = str(ctrader["host_type"])

    service = data.get("service", {})
    if service.get("name"):
        mapping["MARKET_DATA_SERVER_NAME"] = str(service["name"])
    if service.get("host"):
        mapping["MARKET_DATA_SERVER_HOST"] = str(service["host"])
    if service.get("port") is not None:
        mapping["MARKET_DATA_SERVER_PORT"] = str(service["port"])
    if service.get("ds_control_port") is not None:
        mapping["DS_CONTROL_PORT"] = str(service["ds_control_port"])
    if service.get("api_port") is not None:
        mapping["MARKET_DATA_API_PORT"] = str(service["api_port"])
    if service.get("log_level"):
        mapping["MARKET_DATA_LOG_LEVEL"] = str(service["log_level"])
    if service.get("tick_buffer_size") is not None:
        mapping["MARKET_DATA_TICK_BUFFER_SIZE"] = str(service["tick_buffer_size"])
    if service.get("reconnect_delay") is not None:
        mapping["MARKET_DATA_RECONNECT_DELAY_SECONDS"] = str(service["reconnect_delay"])
    if service.get("max_reconnect_delay") is not None:
        mapping["MARKET_DATA_MAX_RECONNECT_DELAY_SECONDS"] = str(service["max_reconnect_delay"])
    if service.get("tick_flush_batch_size") is not None:
        mapping["MARKET_DATA_TICK_FLUSH_BATCH_SIZE"] = str(service["tick_flush_batch_size"])
    if service.get("bar_flush_batch_size") is not None:
        mapping["MARKET_DATA_BAR_FLUSH_BATCH_SIZE"] = str(service["bar_flush_batch_size"])
    if service.get("flush_interval_seconds") is not None:
        mapping["MARKET_DATA_FLUSH_INTERVAL_SECONDS"] = str(service["flush_interval_seconds"])

    return mapping


def _load_dotenv_keys(path: Path) -> set[str]:
    """Return the set of keys that have non-empty values in the .env file."""
    keys: set[str] = set()
    if not path.exists():
        return keys
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if value:
                        keys.add(key)
    except Exception:
        pass
    return keys


def _merge_yaml_into_env(path: Path = DEFAULT_YAML_PATH) -> None:
    """Inject YAML defaults into os.environ only for keys not already present.

    Precedence (highest first):
      1. Explicit environment variables
      2. .env file
      3. YAML config file

    To preserve that order, we skip YAML defaults for any key that is already
    set in os.environ OR already has a value in .env.
    """
    env_file_path = DEFAULT_ENV_PATH
    dotenv_keys = _load_dotenv_keys(env_file_path)
    yaml_values = _load_yaml_config(path)
    for key, value in yaml_values.items():
        if os.environ.get(key) in (None, "") and key not in dotenv_keys:
            os.environ[key] = value
    if yaml_values:
        logger.debug("Loaded %d defaults from YAML: %s", len(yaml_values), path)


def save_yaml_config(updates: dict[str, object], path: Path | None = None) -> None:
    """Persist selected keys back to the workspace YAML config.

    Merges ``updates`` into the existing YAML structure. Only non-None values
    are written; ``None`` or empty-string values are skipped so callers can
    selectively update keys without clobbering others.

    Used by the auth flow to persist grant_id / account_id after a successful
    cTrader OAuth signin.
    """
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed — cannot save YAML config")
        return
    target = path or DEFAULT_YAML_PATH
    data: dict[str, Any] = {}
    if target.exists():
        try:
            with target.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            data = {}

    # Map flat env-style keys back to nested YAML structure
    key_to_section: dict[str, tuple[str, str]] = {
        "CTRADER_AUTH_BROKER_URL": ("ctrader", "broker_url"),
        "CTRADER_AUTH_GRANT_ID": ("ctrader", "grant_id"),
        "CTRADER_CLIENT_ID": ("ctrader", "client_id"),
        "CTRADER_CLIENT_SECRET": ("ctrader", "client_secret"),
        "INTERNAL_API_KEY": ("ctrader", "internal_api_key"),
        "CTRADER_USE_APPWRITE_AUTH": ("ctrader", "use_appwrite_auth"),
        "CTRADER_APPWRITE_USERNAME": ("ctrader", "appwrite_username"),
        "CTRADER_AUTH_DATABASE_ID": ("ctrader", "auth_database_id"),
        "SLAVE_ACCOUNTS_TABLE": ("ctrader", "slave_accounts_table"),
        "ACCOUNT_EVENTS_TABLE": ("ctrader", "account_events_table"),
        "CTRADER_ACCOUNT_ID": ("ctrader", "account_id"),
        "CTRADER_HOST_TYPE": ("ctrader", "host_type"),
        "MARKET_DATA_DB_BACKEND": ("database", "backend"),
        "MARKET_DATA_SQLITE_PATH": ("database", "sqlite_path"),
        "MARKET_DATA_APPWRITE_ENDPOINT": ("database", "appwrite_endpoint"),
        "MARKET_DATA_APPWRITE_PROJECT_ID": ("database", "appwrite_project_id"),
        "MARKET_DATA_APPWRITE_DATABASE_ID": ("database", "appwrite_database_id"),
        "MARKET_DATA_APPWRITE_API_KEY": ("database", "appwrite_api_key"),
        "MARKET_DATA_INFLUXDB_HOST": ("database", "influxdb_host"),
        "MARKET_DATA_INFLUXDB_TOKEN": ("database", "influxdb_token"),
        "MARKET_DATA_INFLUXDB_ORG": ("database", "influxdb_org"),
        "MARKET_DATA_INFLUXDB_DATABASE": ("database", "influxdb_database"),
        "MARKET_DATA_INFLUXDB_RETENTION_DAYS": ("database", "influxdb_retention_days"),
        "MARKET_DATA_INFLUXDB_WRITE_BATCH_SIZE": ("database", "influxdb_write_batch_size"),
        "MARKET_DATA_INFLUXDB_MAX_WRITE_BYTES_PER_SEC": ("database", "influxdb_max_write_bytes_per_sec"),
        "MARKET_DATA_INFLUXDB_WRITE_BURST_BYTES": ("database", "influxdb_write_burst_bytes"),
        "MARKET_DATA_INFLUXDB_MAX_READ_BYTES_PER_SEC": ("database", "influxdb_max_read_bytes_per_sec"),
        "MARKET_DATA_INFLUXDB_SIDECAR_PATH": ("database", "influxdb_sidecar_path"),
        "MARKET_DATA_TICK_FLUSH_BATCH_SIZE": ("service", "tick_flush_batch_size"),
        "MARKET_DATA_BAR_FLUSH_BATCH_SIZE": ("service", "bar_flush_batch_size"),
        "MARKET_DATA_FLUSH_INTERVAL_SECONDS": ("service", "flush_interval_seconds"),
    }
    for flat_key, value in updates.items():
        if value is None or value == "":
            continue
        if flat_key in key_to_section:
            section, field_name = key_to_section[flat_key]
            section_dict = data.setdefault(section, {})
            if field_name == "account_id":
                try:
                    section_dict[field_name] = int(str(value))
                except (ValueError, TypeError):
                    section_dict[field_name] = str(value)
            else:
                section_dict[field_name] = str(value)

    try:
        with target.open("w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        logger.info("Saved config updates to YAML: %s", target)
    except Exception as exc:
        logger.error("Failed to save YAML config: %s", exc)


class Settings(BaseSettings):
    """Application settings loaded from environment.

    Env vars follow the naming in the project's .env file:
    - MARKET_DATA_* for service/InfluxDB settings
    - CTRADER_* for cTrader API credentials
    """

    model_config = SettingsConfigDict(
        env_file=str(DEFAULT_ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Database backend
    db_backend: str = Field(
        default="influxdb",
        alias="MARKET_DATA_DB_BACKEND",
        description="Database backend: sqlite, appwrite, or influxdb",
    )
    sqlite_path: str | None = Field(
        default=None,
        alias="MARKET_DATA_SQLITE_PATH",
        description="Path to SQLite database file (default: project_root/market_data.db)",
    )

    # Appwrite
    appwrite_endpoint: str = Field(
        default="https://sgp.cloud.appwrite.io/v1",
        alias="MARKET_DATA_APPWRITE_ENDPOINT",
    )
    appwrite_project_id: str = Field(
        default="",
        alias="MARKET_DATA_APPWRITE_PROJECT_ID",
    )
    appwrite_database_id: str = Field(
        default="market_data",
        alias="MARKET_DATA_APPWRITE_DATABASE_ID",
    )
    appwrite_api_key: str = Field(
        default="",
        alias="MARKET_DATA_APPWRITE_API_KEY",
    )

    # InfluxDB Cloud Serverless
    influxdb_host: str = Field(
        default="",
        alias="MARKET_DATA_INFLUXDB_HOST",
        description="InfluxDB Cloud Serverless hostname (e.g. https://us-east-1-1.aws.cloud2.influxdata.com)",
    )
    influxdb_token: str = Field(
        default="",
        alias="MARKET_DATA_INFLUXDB_TOKEN",
    )
    influxdb_org: str | None = Field(
        default=None,
        alias="MARKET_DATA_INFLUXDB_ORG",
    )
    influxdb_database: str = Field(
        default="market_data",
        alias="MARKET_DATA_INFLUXDB_DATABASE",
        description="InfluxDB database/bucket name",
    )
    influxdb_retention_days: int = Field(
        default=30,
        alias="MARKET_DATA_INFLUXDB_RETENTION_DAYS",
    )
    influxdb_write_batch_size: int = Field(
        default=100,
        alias="MARKET_DATA_INFLUXDB_WRITE_BATCH_SIZE",
    )
    influxdb_max_write_bytes_per_sec: float = Field(
        default=10_240.0,
        alias="MARKET_DATA_INFLUXDB_MAX_WRITE_BYTES_PER_SEC",
        description="Target write throughput in bytes/second (free tier ~17 KB/s)",
    )
    influxdb_write_burst_bytes: float = Field(
        default=17_408.0,
        alias="MARKET_DATA_INFLUXDB_WRITE_BURST_BYTES",
        description="Max bytes that can be written in a single burst",
    )
    influxdb_max_read_bytes_per_sec: float = Field(
        default=819_200.0,
        alias="MARKET_DATA_INFLUXDB_MAX_READ_BYTES_PER_SEC",
        description="Target read throughput in bytes/second (free tier ~1,000 KB/s)",
    )
    influxdb_sidecar_path: str | None = Field(
        default=None,
        alias="MARKET_DATA_INFLUXDB_SIDECAR_PATH",
        description="Path to SQLite sidecar for metadata when using InfluxDB backend",
    )

    # cTrader - Direct OAuth mode (legacy)
    ctrader_client_id: str | None = Field(
        default=None,
        alias="CTRADER_CLIENT_ID",
    )
    ctrader_client_secret: str | None = Field(
        default=None,
        alias="CTRADER_CLIENT_SECRET",
    )
    ctrader_access_token: str | None = Field(
        default=None,
        alias="CTRADER_ACCESS_TOKEN",
    )
    ctrader_refresh_token: str | None = Field(
        default=None,
        alias="CTRADER_REFRESH_TOKEN",
    )

    # cTrader - Broker-based auth mode (recommended)
    ctrader_auth_broker_url: str | None = Field(
        default=None,
        alias="CTRADER_AUTH_BROKER_URL",
        description="URL of auth broker (e.g., https://internal.mrme.tech)",
    )
    ctrader_auth_grant_id: str | None = Field(
        default=None,
        alias="CTRADER_AUTH_GRANT_ID",
        description="Grant ID from broker authentication",
    )
    ctrader_internal_api_key: str | None = Field(
        default=None,
        alias="INTERNAL_API_KEY",
        description="Internal API key for api-internal Appwrite Function",
    )
    ctrader_use_appwrite_auth: bool = Field(
        default=False,
        alias="CTRADER_USE_APPWRITE_AUTH",
        description="Use Appwrite-based auth (AppwriteTokenManager) instead of legacy BrokerTokenManager",
    )
    ctrader_appwrite_username: str | None = Field(
        default=None,
        alias="CTRADER_APPWRITE_USERNAME",
        description="Preferred slave username for Appwrite-native data service mode",
    )
    ctrader_auth_database_id: str = Field(
        default="ctrader_auth",
        alias="CTRADER_AUTH_DATABASE_ID",
        description="Appwrite database ID holding slave_accounts and account_events",
    )
    slave_accounts_table: str = Field(
        default="slave_accounts",
        alias="SLAVE_ACCOUNTS_TABLE",
    )
    account_events_table: str = Field(
        default="account_events",
        alias="ACCOUNT_EVENTS_TABLE",
    )

    # Account info
    ctrader_account_id: int | None = Field(
        default=None,
        alias="CTRADER_ACCOUNT_ID",
    )
    ctrader_use_live: bool = Field(
        default=False,
        alias="CTRADER_HOST_TYPE",
    )
    ctrader_transport_type: str = Field(
        default="tcp",
        alias="CTRADER_TRANSPORT_TYPE",
    )

    # Service
    service_name: str = Field(
        default="market-data-service",
        alias="MARKET_DATA_SERVER_NAME",
    )
    server_host: str = Field(
        default="0.0.0.0",
        alias="MARKET_DATA_SERVER_HOST",
    )
    server_port: int = Field(
        default=9001,
        alias="MARKET_DATA_SERVER_PORT",
    )
    ds_control_port: int = Field(
        default=9000,
        alias="DS_CONTROL_PORT",
    )
    api_port: int = Field(
        default=9002,
        alias="MARKET_DATA_API_PORT",
    )
    log_level: str = Field(
        default="INFO",
        alias="MARKET_DATA_LOG_LEVEL",
    )
    tick_buffer_size: int = Field(
        default=10_000,
        alias="MARKET_DATA_TICK_BUFFER_SIZE",
    )
    bar_buffer_size: int = Field(
        default=5_000,
        alias="MARKET_DATA_BAR_BUFFER_SIZE",
    )
    depth_buffer_size: int = Field(
        default=1_000,
        alias="MARKET_DATA_DEPTH_BUFFER_SIZE",
    )
    tick_flush_batch_size: int = Field(
        default=100,
        alias="MARKET_DATA_TICK_FLUSH_BATCH_SIZE",
        description="Number of ticks to batch before flushing to the database",
    )
    bar_flush_batch_size: int = Field(
        default=20,
        alias="MARKET_DATA_BAR_FLUSH_BATCH_SIZE",
        description="Number of bars to batch before flushing to the database",
    )
    flush_interval_seconds: float = Field(
        default=5.0,
        alias="MARKET_DATA_FLUSH_INTERVAL_SECONDS",
        description="Maximum seconds to wait before flushing buffered market data",
    )
    default_bar_capacity: int = 500

    # Gold Quantitative Analysis
    gold_quant_enabled: bool = Field(
        default=True,
        alias="GOLD_QUANT_ENABLED",
        description="Enable the gold quantitative analysis engine",
    )
    gold_quant_symbol: str = Field(
        default="XAUUSD",
        alias="GOLD_QUANT_SYMBOL",
    )
    gold_quant_timeframes: str = Field(
        default="M15,H1,H4",
        alias="GOLD_QUANT_TIMEFRAMES",
        description="Comma-separated timeframes for gold quant analysis",
    )
    gold_quant_tick_window: int = Field(
        default=1000,
        alias="GOLD_QUANT_TICK_WINDOW",
    )
    gold_quant_delta_std_threshold: float = Field(
        default=2.0,
        alias="GOLD_QUANT_DELTA_STD_THRESHOLD",
    )
    gold_quant_imbalance_threshold: float = Field(
        default=0.30,
        alias="GOLD_QUANT_IMBALANCE_THRESHOLD",
    )
    gold_quant_min_confluence_tfs: int = Field(
        default=3,
        alias="GOLD_QUANT_MIN_CONFLUENCE_TFS",
    )
    gold_quant_short_reject_threshold: float = Field(
        default=0.30,
        alias="GOLD_QUANT_SHORT_REJECT_THRESHOLD",
    )
    gold_quant_limit_min_confidence: float = Field(
        default=0.60,
        alias="GOLD_QUANT_LIMIT_MIN_CONFIDENCE",
    )

    # Feed / Reconnect
    reconnect_delay: float = Field(
        default=5.0,
        alias="MARKET_DATA_RECONNECT_DELAY_SECONDS",
    )
    max_reconnect_delay: float = Field(
        default=60.0,
        alias="MARKET_DATA_MAX_RECONNECT_DELAY_SECONDS",
    )

    # Data Quality
    stale_threshold_seconds: float = Field(
        default=30.0,
        alias="MARKET_DATA_STALE_THRESHOLD_SECONDS",
    )
    gap_fill_enabled: bool = Field(
        default=True,
        alias="MARKET_DATA_GAP_FILL_ENABLED",
    )
    max_gap_fill_bars: int = Field(
        default=1000,
        alias="MARKET_DATA_MAX_GAP_FILL_BARS",
    )

    @field_validator("ctrader_use_live", mode="before")
    @classmethod
    def _parse_host_type(cls, v: object) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() == "live"
        return bool(v)

    @field_validator("ctrader_account_id", mode="before")
    @classmethod
    def _parse_account_id(cls, v: object) -> int | None:
        """Treat empty strings and non-numeric values as None (unconfigured)."""
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            try:
                return int(s)
            except ValueError:
                return None
        return None

    @property
    def has_ctrader_credentials(self) -> bool:
        """Check if cTrader credentials are available in either mode."""
        # Appwrite-native mode discovers the account from Appwrite tables.
        if self.ctrader_use_appwrite_auth:
            return all(
                [
                    self.ctrader_client_id,
                    self.ctrader_client_secret,
                    self.ctrader_auth_broker_url,
                    self.ctrader_internal_api_key,
                ]
            )
        if not self.ctrader_account_id:
            return False
        # Broker mode: needs auth broker URL, grant ID, and app credentials
        # (client_id/secret are still required for ProtoOAApplicationAuthReq)
        if self.ctrader_auth_broker_url and self.ctrader_auth_grant_id:
            return bool(self.ctrader_client_id and self.ctrader_client_secret)
        # Direct mode: needs full OAuth credentials
        return all(
            [
                self.ctrader_client_id,
                self.ctrader_client_secret,
                self.ctrader_access_token,
            ]
        )

    @property
    def auth_mode(self) -> str:
        """Determine which auth mode is configured."""
        if self.ctrader_use_appwrite_auth:
            if self.ctrader_auth_broker_url and self.ctrader_internal_api_key:
                return "appwrite"
            return "appwrite-incomplete"
        if self.ctrader_auth_broker_url and self.ctrader_auth_grant_id:
            return "broker"
        if self.ctrader_client_id and self.ctrader_client_secret and self.ctrader_access_token:
            return "direct"
        if self.ctrader_access_token:
            return "raw"
        return "none"


@lru_cache
def get_settings() -> Settings:
    _merge_yaml_into_env()
    return Settings()


def get_preset_path(name: str) -> Path:
    """Return path to a preset configuration file."""
    base = Path(__file__).parent / "presets"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{name}.json"
