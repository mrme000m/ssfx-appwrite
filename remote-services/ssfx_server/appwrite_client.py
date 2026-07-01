"""Shared Appwrite server client."""
from __future__ import annotations

from appwrite.client import Client
from appwrite.services.tables_db import TablesDB

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
        self.client = Client()
        self.client.set_endpoint(config.appwrite_endpoint)
        self.client.set_project(config.appwrite_project_id)
        self.client.set_key(config.appwrite_api_key)
        self.tables_db = TablesDB(self.client)
        self.database_id = config.appwrite_database_id
        self.accounts_table = config.appwrite_accounts_table
        self.presets_table = config.appwrite_presets_table
        self.executions_table = config.appwrite_executions_table

    @classmethod
    def reset(cls) -> None:
        cls._instance = None
