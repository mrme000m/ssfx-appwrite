"""Runtime configuration for the AI agent harness."""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentHarnessSettings(BaseSettings):
    """Settings loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=os.environ.get("SSFX_ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    agent_harness_port: int = Field(default=9003, alias="AGENT_HARNESS_PORT")
    agent_harness_host: str = Field(default="0.0.0.0", alias="AGENT_HARNESS_HOST")
    cors_origins: str = Field(
        default="https://command.mrme.tech,https://admin.mrme.tech,http://localhost:3000,http://localhost:8001",
        alias="AGENT_HARNESS_CORS_ORIGINS",
    )

    # LLM provider (generic fallback for Hermes / Kimi via OpenRouter)
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(default="https://openrouter.ai/api/v1", alias="LLM_BASE_URL")

    # Mistral AI native API (SignalIntentAgent). Falls back to llm_api_key / llm_base_url if not set.
    mistralai_api_key: str = Field(default="", alias="MISTRALAI_API_KEY")
    mistralai_base_url: str = Field(default="https://api.mistral.ai/v1", alias="MISTRALAI_BASE_URL")

    # Model slugs
    agent_model_mistral: str = Field(
        default="mistralai/mistral-small-3.2-24b-instruct", alias="AGENT_MODEL_MISTRAL"
    )
    agent_model_hermes: str = Field(
        default="nousresearch/hermes-3-llama-3.1-405b", alias="AGENT_MODEL_HERMES"
    )
    agent_model_kimi: str = Field(
        default="moonshotai/kimi-k2.7-code", alias="AGENT_MODEL_KIMI"
    )

    # Optional local endpoints for hybrid hosting
    agent_local_base_url_mistral: str = Field(default="", alias="AGENT_LOCAL_BASE_URL_MISTRAL")
    agent_local_base_url_hermes: str = Field(default="", alias="AGENT_LOCAL_BASE_URL_HERMES")
    agent_local_base_url_kimi: str = Field(default="", alias="AGENT_LOCAL_BASE_URL_KIMI")

    # Upstream services
    data_service_base_url: str = Field(default="http://localhost:9002", alias="DATA_SERVICE_BASE_URL")
    data_service_api_key: str = Field(default="", alias="DATA_SERVICE_API_KEY")
    account_hub_url: str = Field(default="http://localhost:9301", alias="ACCOUNT_HUB_URL")
    admin_api_key: str = Field(default="", alias="ADMIN_API_KEY")

    # PPLX Agent (long-term gold market picture / space research)
    pplx_agent_url: str = Field(default="http://localhost:9004", alias="PPLX_AGENT_URL")
    pplx_agent_enabled: bool = Field(default=True, alias="PPLX_AGENT_ENABLED")

    # Kill switches / thresholds
    agent_entry_enabled: bool = Field(default=True, alias="AGENT_ENTRY_ENABLED")
    agent_lifecycle_enabled: bool = Field(default=True, alias="AGENT_LIFECYCLE_ENABLED")
    agent_autonomy_enabled: bool = Field(default=False, alias="AGENT_AUTONOMY_ENABLED")
    agent_min_entry_confidence: float = Field(default=0.65, alias="AGENT_MIN_ENTRY_CONFIDENCE")
    agent_min_limit_confidence: float = Field(default=0.60, alias="AGENT_MIN_LIMIT_CONFIDENCE")
    agent_max_latency_ms: int = Field(default=5000, alias="AGENT_MAX_LATENCY_MS")
    agent_mistral_timeout_ms: int = Field(default=2000, alias="AGENT_MISTRAL_TIMEOUT_MS")
    agent_hermes_timeout_ms: int = Field(default=5000, alias="AGENT_HERMES_TIMEOUT_MS")
    agent_kimi_timeout_ms: int = Field(default=10000, alias="AGENT_KIMI_TIMEOUT_MS")

    def base_url_for(self, model_slug: str) -> str:
        """Return the effective base URL for a model (local → provider-specific → generic fallback)."""
        mapping = {
            self.agent_model_mistral: self.agent_local_base_url_mistral,
            self.agent_model_hermes: self.agent_local_base_url_hermes,
            self.agent_model_kimi: self.agent_local_base_url_kimi,
        }
        local = mapping.get(model_slug, "")
        if local:
            return local.rstrip("/")

        if model_slug == self.agent_model_mistral and self.mistralai_base_url:
            return self.mistralai_base_url.rstrip("/")

        return self.llm_base_url.rstrip("/")

    def api_key_for(self, model_slug: str) -> str:
        """Return the effective API key for a model (provider-specific → generic fallback)."""
        mapping = {
            self.agent_model_mistral: self.agent_local_base_url_mistral,
            self.agent_model_hermes: self.agent_local_base_url_hermes,
            self.agent_model_kimi: self.agent_local_base_url_kimi,
        }
        if mapping.get(model_slug):
            return ""

        if model_slug == self.agent_model_mistral and self.mistralai_api_key:
            return self.mistralai_api_key

        return self.llm_api_key


@lru_cache
def get_settings() -> AgentHarnessSettings:
    return AgentHarnessSettings()
