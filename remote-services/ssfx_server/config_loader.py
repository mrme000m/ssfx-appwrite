"""Load server configuration from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from ssfx_parser import AgentConfig


@dataclass
class ServerConfig:
    telegram_bot_token: str
    telegram_webhook_secret_token: str
    source_chat_id: str
    webhook_host: str
    webhook_port: int
    webhook_path: str
    llm_api_key: str
    llm_model: str
    llm_base_url: str
    log_level: str
    appwrite_endpoint: str
    appwrite_project_id: str
    appwrite_api_key: str
    appwrite_database_id: str
    appwrite_accounts_table: str
    appwrite_presets_table: str
    appwrite_executions_table: str
    appwrite_risk_state_table: str
    dataservice_base_url: str
    dataservice_api_key: str
    agent_harness_base_url: str
    agent_intent_enabled: bool
    ctrader_broker_url: str
    admin_site_origin: str
    admin_api_key: str
    signal_experience_enabled: bool
    signal_experience_database_id: str
    signal_experience_block_threshold: float
    signal_experience_reduce_threshold: float
    agent_autonomy_enabled: bool
    gold_quant_signal_enabled: bool
    gold_quant_signal_interval_sec: float
    gold_quant_min_confidence: float
    gold_quant_agent_min_confidence: float
    signal_store_backend: str
    signal_store_appwrite_database_id: str
    appwrite_raw_messages_table: str
    appwrite_parsed_signals_table: str
    appwrite_signal_trades_table: str
    signal_store_sqlite_path: str
    signal_store_fallback_sqlite: bool
    signal_webhook_secret: str

    @property
    def webhook_url(self) -> str:
        host = self.webhook_host.rstrip("/")
        path = self.webhook_path if self.webhook_path.startswith("/") else f"/{self.webhook_path}"
        return f"{host}{path}"

    @property
    def is_polling_mode(self) -> bool:
        return self.webhook_host.lower() == "polling"

    def require_webhook_secret(self) -> None:
        """Raise if webhook mode is enabled but no secret token is configured."""
        if self.is_polling_mode:
            return
        if not self.telegram_webhook_secret_token:
            raise ValueError(
                "TELEGRAM_WEBHOOK_SECRET_TOKEN is required when WEBHOOK_HOST is not 'polling'. "
                "Generate a random value (e.g. `openssl rand -hex 32`) and set it via setWebhook."
            )

    def agent_config(self) -> AgentConfig:
        return AgentConfig(
            base_url=self.llm_base_url,
            model=self.llm_model,
            temperature=0.1,
            max_tokens=1000,
            timeout_seconds=30,
            min_confidence=0.75,
            api_key=self.llm_api_key,
        )


def load_config(env_file: str | None = None) -> ServerConfig:
    if env_file:
        load_dotenv(env_file, override=False)
    else:
        # In the unified container, config is at /app/config/v2.env
        default_env = os.environ.get("SSFX_ENV_FILE", "/app/config/v2.env")
        if os.path.isfile(default_env):
            load_dotenv(default_env, override=False)
        else:
            load_dotenv(override=False)

    def _env(key: str, default: str = "") -> str:
        val = os.getenv(key)
        return val if val and val.strip() else default

    return ServerConfig(
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_webhook_secret_token=_env("TELEGRAM_WEBHOOK_SECRET_TOKEN", ""),
        source_chat_id=_env("SOURCE_CHAT_ID", "-1001661400724"),
        webhook_host=_env("WEBHOOK_HOST", "https://example.com"),
        webhook_port=int(_env("SSFX_SERVER_PORT") or _env("PORT") or "8000"),
        webhook_path=_env("WEBHOOK_PATH", "/webhook"),
        llm_api_key=_env("LLM_API_KEY"),
        llm_model=_env("LLM_MODEL", "nvidia/nemotron-3-super-120b-a12b:free"),
        llm_base_url=_env("LLM_BASE_URL", "https://openrouter.ai/api/v1"),
        log_level=_env("LOG_LEVEL", "INFO"),
        appwrite_endpoint=_env("APPWRITE_ENDPOINT", "https://sgp.cloud.appwrite.io/v1"),
        appwrite_project_id=_env("APPWRITE_PROJECT_ID", "6a22a362002b9ae880bb"),
        appwrite_api_key=_env("APPWRITE_API_KEY"),
        appwrite_database_id=_env("APPWRITE_DATABASE_ID", "slwp_platform"),
        appwrite_accounts_table=_env("APPWRITE_ACCOUNTS_TABLE", "signal_slaves"),
        appwrite_presets_table=_env("APPWRITE_PRESETS_TABLE", "ssfx_presets"),
        appwrite_executions_table=_env("APPWRITE_EXECUTIONS_TABLE", "ssfx_executions"),
        appwrite_risk_state_table=_env("APPWRITE_RISK_STATE_TABLE", "ssfx_risk_state"),
        dataservice_base_url=_env("DATA_SERVICE_URL", "http://127.0.0.1:9002"),
        dataservice_api_key=_env("DATA_SERVICE_API_KEY"),
        agent_harness_base_url=_env("AGENT_HARNESS_URL", "http://127.0.0.1:9003"),
        agent_intent_enabled=_env("AGENT_INTENT_ENABLED", "true").lower() == "true",
        ctrader_broker_url=_env("CTRADER_BROKER_URL", ""),
        admin_site_origin=_env("ADMIN_SITE_ORIGIN", "https://app.mrme.tech"),
        admin_api_key=_env("ADMIN_API_KEY", ""),
        signal_experience_enabled=_env("SIGNAL_EXPERIENCE_ENABLED", "true").lower() == "true",
        signal_experience_database_id=_env("SIGNAL_EXPERIENCE_DATABASE_ID", "market_data"),
        signal_experience_block_threshold=float(_env("SIGNAL_EXPERIENCE_BLOCK_THRESHOLD", "0.50")),
        signal_experience_reduce_threshold=float(_env("SIGNAL_EXPERIENCE_REDUCE_THRESHOLD", "0.75")),
        agent_autonomy_enabled=_env("AGENT_AUTONOMY_ENABLED", "false").lower() == "true",
        gold_quant_signal_enabled=_env("GOLD_QUANT_SIGNAL_ENABLED", "false").lower() == "true",
        gold_quant_signal_interval_sec=float(_env("GOLD_QUANT_SIGNAL_INTERVAL_SEC", "60")),
        gold_quant_min_confidence=float(_env("GOLD_QUANT_MIN_CONFIDENCE", "0.75")),
        gold_quant_agent_min_confidence=float(_env("GOLD_QUANT_AGENT_MIN_CONFIDENCE", "0.65")),
        signal_store_backend=_env("SIGNAL_STORE_BACKEND", "appwrite"),
        signal_store_appwrite_database_id=_env("APPWRITE_SIGNAL_DATABASE_ID", "market_data"),
        appwrite_raw_messages_table=_env("APPWRITE_RAW_MESSAGES_TABLE", "raw_messages"),
        appwrite_parsed_signals_table=_env("APPWRITE_PARSED_SIGNALS_TABLE", "parsed_signals"),
        appwrite_signal_trades_table=_env("APPWRITE_SIGNAL_TRADES_TABLE", "signal_trades"),
        signal_store_sqlite_path=_env("SIGNAL_STORE_SQLITE_PATH", "/app/data/signals.db"),
        signal_store_fallback_sqlite=_env("SIGNAL_STORE_FALLBACK_SQLITE", "true").lower() == "true",
        signal_webhook_secret=_env("SIGNAL_WEBHOOK_SECRET", ""),
    )
