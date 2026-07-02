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
    mongo_uri: str
    mongo_database: str
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

    @property
    def webhook_url(self) -> str:
        host = self.webhook_host.rstrip("/")
        path = self.webhook_path if self.webhook_path.startswith("/") else f"/{self.webhook_path}"
        return f"{host}{path}"

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
        mongo_uri=_env("MONGODB_URI", "mongodb://localhost:27017"),
        mongo_database=_env("MONGODB_DATABASE", "ssfx_v2"),
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
        appwrite_database_id=_env("APPWRITE_DATABASE_ID", "6a44553f0034baa503c8"),
        appwrite_accounts_table=_env("APPWRITE_ACCOUNTS_TABLE", "ssfx_accounts"),
        appwrite_presets_table=_env("APPWRITE_PRESETS_TABLE", "ssfx_presets"),
        appwrite_executions_table=_env("APPWRITE_EXECUTIONS_TABLE", "ssfx_executions"),
        dataservice_base_url=_env("DATA_SERVICE_URL", "http://127.0.0.1:9099"),
        dataservice_api_key=_env("DATA_SERVICE_API_KEY"),
        agent_harness_base_url=_env("AGENT_HARNESS_URL", "http://127.0.0.1:9003"),
        agent_intent_enabled=_env("AGENT_INTENT_ENABLED", "true").lower() == "true",
        ctrader_broker_url=_env("CTRADER_BROKER_URL", "https://auth-ctrader.mrme0.store"),
        admin_site_origin=_env("ADMIN_SITE_ORIGIN", "https://command.mrme.tech"),
        admin_api_key=_env("ADMIN_API_KEY", ""),
        signal_experience_enabled=_env("SIGNAL_EXPERIENCE_ENABLED", "true").lower() == "true",
        signal_experience_database_id=_env("SIGNAL_EXPERIENCE_DATABASE_ID", "market_data"),
        signal_experience_block_threshold=float(_env("SIGNAL_EXPERIENCE_BLOCK_THRESHOLD", "0.50")),
        signal_experience_reduce_threshold=float(_env("SIGNAL_EXPERIENCE_REDUCE_THRESHOLD", "0.75")),
    )
