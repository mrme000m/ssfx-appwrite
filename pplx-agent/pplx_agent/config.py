"""Configuration for the PPLX Agent.

The canonical runtime configuration lives in the Appwrite ``service_config``
table (``config_key = pplx_agent``). For local development the same keys can be
overridden through environment variables / ``pplx-agent/.env``.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class PplxAgentSettings(BaseSettings):
    """Runtime settings for the Perplexity gold-intelligence agent."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Perplexity auth (cookies loaded from Bitwarden by default)
    pplx_bw_item_name: str = Field(default="perplexity.ai", alias="PPLX_BW_ITEM_NAME")
    pplx_mode: str = Field(default="pro", alias="PPLX_MODE")
    pplx_model: str = Field(default="GPT-5.5", alias="PPLX_MODEL")
    pplx_thinking: bool = Field(default=True, alias="PPLX_THINKING")
    pplx_timeout: int = Field(default=120, alias="PPLX_TIMEOUT")

    # Gold market space
    gold_market_space_uuid: str = Field(default="", alias="GOLD_MARKET_SPACE_UUID")
    gold_market_space_name: str = Field(default="Gold Market Intelligence", alias="GOLD_MARKET_SPACE_NAME")

    # TradingView free-tier credentials (optional; required only for private scripts)
    tv_user: str = Field(default="", alias="TV_USER")
    tv_session: str = Field(default="", alias="TV_SESSION")
    tv_signature: str = Field(default="", alias="TV_SIGNATURE")

    # Target symbol / timeframes
    analysis_symbol: str = Field(default="XAUUSD", alias="ANALYSIS_SYMBOL")
    analysis_timeframes: str = Field(default="1,5,15,60,240,D,W,M", alias="ANALYSIS_TIMEFRAMES")

    # Upstream integrations
    data_service_base_url: str = Field(default="http://localhost:9002", alias="DATA_SERVICE_BASE_URL")
    data_service_api_key: str = Field(default="", alias="DATA_SERVICE_API_KEY")
    agent_harness_url: str = Field(default="http://localhost:9003", alias="AGENT_HARNESS_URL")

    # Output / scheduling
    report_output_dir: Path = Field(default=_PROJECT_ROOT / "reports", alias="REPORT_OUTPUT_DIR")
    cache_dir: Path = Field(default=_PROJECT_ROOT / ".cache", alias="CACHE_DIR")
    log_dir: Path = Field(default=_PROJECT_ROOT / "logs", alias="LOG_DIR")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    report_retention_days: int = Field(default=90, alias="REPORT_RETENTION_DAYS")

    # API server
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=9004, alias="API_PORT")

    # Feature flags
    enable_macro: bool = Field(default=True, alias="PPLX_ENABLE_MACRO")
    enable_technical: bool = Field(default=True, alias="PPLX_ENABLE_TECHNICAL")
    enable_fundamental: bool = Field(default=True, alias="PPLX_ENABLE_FUNDAMENTAL")
    enable_space_sync: bool = Field(default=True, alias="PPLX_ENABLE_SPACE_SYNC")

    @property
    def timeframes_list(self) -> list[str]:
        return [tf.strip() for tf in self.analysis_timeframes.split(",") if tf.strip()]

    def to_appwrite_payload(self) -> dict[str, Any]:
        """Nested dict suitable for the service_config table."""
        return {
            "perplexity": {
                "bw_item_name": self.pplx_bw_item_name,
                "mode": self.pplx_mode,
                "model": self.pplx_model,
                "thinking": self.pplx_thinking,
                "timeout": self.pplx_timeout,
            },
            "space": {
                "gold_market_uuid": self.gold_market_space_uuid,
                "gold_market_name": self.gold_market_space_name,
            },
            "tradingview": {
                "user": self.tv_user,
                "session": self.tv_session,
                "signature": self.tv_signature,
            },
            "analysis": {
                "symbol": self.analysis_symbol,
                "timeframes": self.timeframes_list,
            },
            "upstream": {
                "data_service_base_url": self.data_service_base_url,
                "data_service_api_key": self.data_service_api_key,
                "agent_harness_url": self.agent_harness_url,
            },
            "output": {
                "report_output_dir": str(self.report_output_dir),
                "cache_dir": str(self.cache_dir),
                "log_dir": str(self.log_dir),
                "log_level": self.log_level,
                "report_retention_days": self.report_retention_days,
            },
            "api": {
                "host": self.api_host,
                "port": self.api_port,
            },
            "features": {
                "enable_macro": self.enable_macro,
                "enable_technical": self.enable_technical,
                "enable_fundamental": self.enable_fundamental,
                "enable_space_sync": self.enable_space_sync,
            },
        }

    @classmethod
    def from_appwrite_config(cls, config_json: dict[str, Any]) -> "PplxAgentSettings":
        """Build settings from the nested service_config payload."""
        flat: dict[str, Any] = {}
        mapping = {
            "perplexity": {
                "bw_item_name": "PPLX_BW_ITEM_NAME",
                "mode": "PPLX_MODE",
                "model": "PPLX_MODEL",
                "thinking": "PPLX_THINKING",
                "timeout": "PPLX_TIMEOUT",
            },
            "space": {
                "gold_market_uuid": "GOLD_MARKET_SPACE_UUID",
                "gold_market_name": "GOLD_MARKET_SPACE_NAME",
            },
            "tradingview": {
                "user": "TV_USER",
                "session": "TV_SESSION",
                "signature": "TV_SIGNATURE",
            },
            "analysis": {
                "symbol": "ANALYSIS_SYMBOL",
                "timeframes": "ANALYSIS_TIMEFRAMES",
            },
            "upstream": {
                "data_service_base_url": "DATA_SERVICE_BASE_URL",
                "data_service_api_key": "DATA_SERVICE_API_KEY",
                "agent_harness_url": "AGENT_HARNESS_URL",
            },
            "output": {
                "report_output_dir": "REPORT_OUTPUT_DIR",
                "cache_dir": "CACHE_DIR",
                "log_dir": "LOG_DIR",
                "log_level": "LOG_LEVEL",
                "report_retention_days": "REPORT_RETENTION_DAYS",
            },
            "api": {
                "host": "API_HOST",
                "port": "API_PORT",
            },
            "features": {
                "enable_macro": "PPLX_ENABLE_MACRO",
                "enable_technical": "PPLX_ENABLE_TECHNICAL",
                "enable_fundamental": "PPLX_ENABLE_FUNDAMENTAL",
                "enable_space_sync": "PPLX_ENABLE_SPACE_SYNC",
            },
        }
        for section, keys in mapping.items():
            section_data = config_json.get(section, {})
            for key, env_name in keys.items():
                if key in section_data:
                    value = section_data[key]
                    # Convert list back to comma-separated for timeframes.
                    if env_name == "ANALYSIS_TIMEFRAMES" and isinstance(value, list):
                        value = ",".join(value)
                    flat[env_name] = value
        return cls(**flat)


def _load_appwrite_config() -> dict[str, Any] | None:
    """Fetch the pplx_agent row from Appwrite TablesDB if credentials are present."""
    project_id = os.environ.get("APPWRITE_PROJECT_ID")
    api_key = os.environ.get("APPWRITE_API_KEY")
    endpoint = os.environ.get("APPWRITE_ENDPOINT", "https://sgp.cloud.appwrite.io/v1")
    database_id = os.environ.get("APPWRITE_DATABASE_ID", "ctrader_auth")

    if not project_id or not api_key:
        return None

    try:
        from appwrite.client import Client
        from appwrite.query import Query
        from appwrite.services.tables_db import TablesDB
    except ImportError:
        logger.debug("appwrite SDK not installed; skipping Appwrite config lookup")
        return None

    try:
        client = Client()
        client.set_endpoint(endpoint)
        client.set_project(project_id)
        client.set_key(api_key)
        tables = TablesDB(client)
        result = tables.list_rows(
            database_id=database_id,
            table_id="service_config",
            queries=[Query.equal("config_key", "pplx_agent")],
        )
        rows = getattr(result, "documents", getattr(result, "rows", []))
        if not rows:
            return None
        row = rows[0]
        if isinstance(row, dict):
            row_data = row
        else:
            # Appwrite SDK Row objects expose fields under `.data`.
            row_data = getattr(row, "data", dict(row) if callable(getattr(row, "keys", None)) else {})
        raw = row_data.get("config_value", "{}") if isinstance(row_data, dict) else "{}"
        return json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except Exception as exc:
        logger.warning("Could not load pplx_agent config from Appwrite: %s", exc)
        return None


@lru_cache(maxsize=1)
def get_settings() -> PplxAgentSettings:
    """Return cached settings, merging Appwrite config and environment variables."""
    appwrite_config = _load_appwrite_config()
    if appwrite_config:
        return PplxAgentSettings.from_appwrite_config(appwrite_config)
    return PplxAgentSettings()


def get_space_uuid() -> str:
    """Return configured gold-market space UUID or raise."""
    uuid = get_settings().gold_market_space_uuid
    if not uuid:
        raise RuntimeError(
            "GOLD_MARKET_SPACE_UUID is not configured. "
            "Run: ./dev.sh pplx-agent setup"
        )
    return uuid
