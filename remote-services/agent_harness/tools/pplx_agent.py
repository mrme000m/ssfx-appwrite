"""Async client for the PPLX Agent research API."""

from __future__ import annotations

from typing import Any, cast

import httpx

from ..config import AgentHarnessSettings, get_settings


class PplxAgentClient:
    """HTTP client for the Perplexity-powered gold market intelligence service."""

    def __init__(self, settings: AgentHarnessSettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = httpx.AsyncClient(timeout=120.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_long_term_picture(self) -> dict[str, Any] | None:
        """Fetch the latest long-term synthesis cached in the PPLX Agent Space."""
        url = f"{self._settings.pplx_agent_url.rstrip('/')}/api/v1/gold/query"
        try:
            resp = await self._client.post(
                url,
                json={
                    "query": "What is the current long-term gold market picture, trend, key levels, and main risks?",
                    "mode": "pro",
                },
            )
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("PPLX Agent query failed: %s", exc)
            return None

    async def trigger_update(self) -> dict[str, Any] | None:
        """Trigger the PPLX Agent daily update."""
        url = f"{self._settings.pplx_agent_url.rstrip('/')}/api/v1/gold/update"
        try:
            resp = await self._client.post(url)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("PPLX Agent update trigger failed: %s", exc)
            return None

    async def health(self) -> dict[str, Any] | None:
        url = f"{self._settings.pplx_agent_url.rstrip('/')}/health"
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("PPLX Agent health check failed: %s", exc)
            return None
