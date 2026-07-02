"""Async client for the market data service REST API."""

from __future__ import annotations

from typing import Any, cast

import httpx

from ..config import AgentHarnessSettings, get_settings


class DataServiceClient:
    """HTTP client for DataService market context and gold quant endpoints."""

    def __init__(self, settings: AgentHarnessSettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_gold_quant_snapshot(self) -> dict[str, Any] | None:
        url = f"{self._settings.data_service_base_url.rstrip('/')}/api/v1/gold/quant"
        try:
            resp = await self._client.get(url, headers=self._auth_headers())
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Failed to fetch gold quant snapshot: %s", exc)
            return None

    async def get_market_context(self, symbol: str) -> dict[str, Any] | None:
        url = f"{self._settings.data_service_base_url.rstrip('/')}/api/v1/context/{symbol}"
        try:
            resp = await self._client.get(url, headers=self._auth_headers())
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Failed to fetch market context: %s", exc)
            return None

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._settings.data_service_api_key:
            headers["Authorization"] = f"Bearer {self._settings.data_service_api_key}"
        return headers
