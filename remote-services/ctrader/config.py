"""Configuration for the unified ctrader service."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

ENV_FILE = os.environ.get("SSFX_ENV_FILE", "/app/config/v2.env")
if os.path.isfile(ENV_FILE):
    load_dotenv(ENV_FILE, override=False)


@dataclass
class CTRADERConfig:
    """Service-level network and security settings."""

    host: str = "127.0.0.1"
    port: int = 9300
    log_level: str = "INFO"

    # Auth
    slave_api_key: str = ""
    admin_api_key: str = ""
    data_api_secret: str = ""

    # Appwrite
    appwrite_endpoint: str = "https://sgp.cloud.appwrite.io/v1"
    appwrite_project_id: str = ""
    appwrite_api_key: str = ""
    appwrite_database_id: str = ""
    executions_table: str = "ssfx_executions"
    events_table: str = "ctrader_trading_events"
    accounts_table: str = "ssfx_accounts"

    # cTrader / auth broker
    ctrader_auth_broker_url: str = "https://internal.mrme.tech"
    internal_api_key: str = ""
    ctrader_client_id: str = ""
    ctrader_client_secret: str = ""

    # Data service
    data_service_url: str = "https://dataservice.mrme.tech"
    data_service_api_key: str = ""
    data_poll_interval_ms: int = 250

    # Account Hub (persistent cTrader connections)
    account_hub_port: int = 9301
    account_hub_enabled: bool = True
    account_hub_poll_interval: float = 30.0
    account_hub_reconnect_base: float = 5.0
    account_hub_reconnect_max: float = 60.0
    slave_accounts_table: str = "slave_accounts"
    account_events_table: str = "account_events"
    ctrader_auth_database_id: str = ""

    # Trading defaults
    default_position_timeout_minutes: float = 5.0
    token_refresh_buffer_seconds: float = 300.0

    @classmethod
    def from_env(cls) -> "CTRADERConfig":
        return cls(
            host=os.getenv("CTRADER_HOST", "127.0.0.1"),
            port=int(os.getenv("CTRADER_PORT", "9300")),
            log_level=os.getenv("CTRADER_LOG_LEVEL", "INFO"),
            slave_api_key=os.getenv("SLAVE_API_KEY", ""),
            admin_api_key=os.getenv("ADMIN_API_KEY", ""),
            data_api_secret=os.getenv("CTRADER_DATA_API_SECRET", ""),
            appwrite_endpoint=os.getenv("APPWRITE_ENDPOINT", "https://sgp.cloud.appwrite.io/v1"),
            appwrite_project_id=os.getenv("APPWRITE_PROJECT_ID", ""),
            appwrite_api_key=os.getenv("APPWRITE_API_KEY", ""),
            appwrite_database_id=os.getenv("APPWRITE_DATABASE_ID", ""),
            executions_table=os.getenv("APPWRITE_EXECUTIONS_TABLE", "ssfx_executions"),
            events_table=os.getenv("APPWRITE_EVENTS_TABLE", "ctrader_trading_events"),
            accounts_table=os.getenv("APPWRITE_ACCOUNTS_TABLE", "ssfx_accounts"),
            ctrader_auth_broker_url=os.getenv("CTRADER_AUTH_BROKER_URL", "https://internal.mrme.tech"),
            internal_api_key=os.getenv("INTERNAL_API_KEY", ""),
            ctrader_client_id=os.getenv("CTRADER_CLIENT_ID", ""),
            ctrader_client_secret=os.getenv("CTRADER_CLIENT_SECRET", ""),
            data_service_url=os.getenv("DATA_SERVICE_URL", "https://dataservice.mrme.tech"),
            data_service_api_key=os.getenv("DATA_SERVICE_API_KEY", ""),
            data_poll_interval_ms=int(os.getenv("DATA_POLL_INTERVAL_MS", "250")),
            account_hub_port=int(os.getenv("ACCOUNT_HUB_PORT", "9301")),
            account_hub_enabled=os.getenv("ACCOUNT_HUB_ENABLED", "true").lower() == "true",
            account_hub_poll_interval=float(os.getenv("ACCOUNT_HUB_POLL_INTERVAL", "30.0")),
            account_hub_reconnect_base=float(os.getenv("ACCOUNT_HUB_RECONNECT_BASE", "5.0")),
            account_hub_reconnect_max=float(os.getenv("ACCOUNT_HUB_RECONNECT_MAX", "60.0")),
            slave_accounts_table=os.getenv("SLAVE_ACCOUNTS_TABLE", "slave_accounts"),
            account_events_table=os.getenv("ACCOUNT_EVENTS_TABLE", "account_events"),
            ctrader_auth_database_id=os.getenv("CTRADER_AUTH_DATABASE_ID", ""),
            default_position_timeout_minutes=float(os.getenv("DEFAULT_POSITION_TIMEOUT_MINUTES", "5.0")),
            token_refresh_buffer_seconds=float(os.getenv("TOKEN_REFRESH_BUFFER_SECONDS", "300.0")),
        )

    def require_appwrite(self) -> None:
        missing = [
            k
            for k in [
                self.appwrite_project_id,
                self.appwrite_api_key,
                self.appwrite_database_id,
            ]
            if not k
        ]
        if missing:
            raise RuntimeError("Appwrite credentials are not configured")

    def require_internal_key(self) -> None:
        if not self.internal_api_key:
            raise RuntimeError("INTERNAL_API_KEY is required for cTrader token refresh")


@dataclass
class SymbolRoute:
    """Parsed URL route for a market or trade request."""

    broker: str
    symbol: str


@dataclass
class TradeRoute:
    """Parsed URL route for a trade request."""

    grant_id: str
    ctid_trader_account_id: int
    action: str
    broker: str = "ctrader"
