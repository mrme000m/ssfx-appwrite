"""Lightweight Appwrite client factory.

Provides a single function ``create_appwrite_client()`` that builds a
configured ``Client`` + ``TablesDB`` from standard environment variables.

No singleton, no heavy server-config imports — just env vars.
"""
from __future__ import annotations

import os
from typing import Any

from appwrite.client import Client
from appwrite.services.tables_db import TablesDB


def create_appwrite_client(
    endpoint: str | None = None,
    project_id: str | None = None,
    api_key: str | None = None,
) -> tuple[Client, TablesDB]:
    """Build an Appwrite Client + TablesDB from env or explicit args.

    Priority: explicit args > environment variables > empty string.
    """
    endpoint = endpoint or os.getenv("APPWRITE_ENDPOINT", "")
    project_id = project_id or os.getenv("APPWRITE_PROJECT_ID", "")
    api_key = api_key or os.getenv("APPWRITE_API_KEY", "")

    client = Client()
    client.set_endpoint(endpoint)
    client.set_project(project_id)
    client.set_key(api_key)
    return client, TablesDB(client)


def get_appwrite_database_id() -> str:
    return os.getenv("APPWRITE_DATABASE_ID", "ctrader_auth")
