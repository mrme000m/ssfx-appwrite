"""Async client for the account hub."""

from __future__ import annotations

from typing import Any, cast

import httpx

from ..config import AgentHarnessSettings, get_settings


class AccountHubClient:
    """HTTP client for account hub account/position state."""

    def __init__(self, settings: AgentHarnessSettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def list_accounts(self) -> list[dict[str, Any]]:
        url = f"{self._settings.account_hub_url.rstrip('/')}/accounts"
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Failed to list accounts: %s", exc)
            return []

    async def get_account(self, grant_id: str, ctid: int) -> dict[str, Any] | None:
        url = f"{self._settings.account_hub_url.rstrip('/')}/accounts/{grant_id}/{ctid}"
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Failed to get account: %s", exc)
            return None
