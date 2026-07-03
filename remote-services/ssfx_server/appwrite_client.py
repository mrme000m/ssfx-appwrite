"""Shared Appwrite server client.

Thin wrapper around the shared factory that also caches the singleton
and exposes table names from server config.
"""
from __future__ import annotations

from shared.appwrite_client import create_appwrite_client

from ssfx_server.config_loader import ServerConfig


class AppwriteClient:
    """Singleton-style Appwrite client initialized from server config."""

    _instance: AppwriteClient | None = None

    def __new__(cls, config: ServerConfig | None = None) -> AppwriteClient:
        if cls._instance is None:
            if config is None:
                from ssfx_server.config_loader import load_config

                config = load_config()
            cls._instance = super().__new__(cls)
            cls._instance._init(config)
        return cls._instance

    def _init(self, config: ServerConfig) -> None:
        self.client, self.tables_db = create_appwrite_client(
            endpoint=config.appwrite_endpoint,
            project_id=config.appwrite_project_id,
            api_key=config.appwrite_api_key,
        )
        self.database_id = config.appwrite_database_id
        self.accounts_table = config.appwrite_accounts_table
        self.presets_table = config.appwrite_presets_table
        self.executions_table = config.appwrite_executions_table
        self.risk_state_table = config.appwrite_risk_state_table

    @classmethod
    def reset(cls) -> None:
        cls._instance = None
