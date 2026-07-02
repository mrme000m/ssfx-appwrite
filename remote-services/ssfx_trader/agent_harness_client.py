"""Async client for the AI agent harness service."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AgentHarnessClient:
    """Calls the agent harness decision endpoints."""

    def __init__(self, base_url: str | None = None, timeout: float = 5.0):
        self.base_url = (base_url or os.environ.get("AGENT_HARNESS_URL", "http://127.0.0.1:9003")).rstrip("/")
        self.timeout = timeout
        self._entry_enabled = os.environ.get("AGENT_ENTRY_ENABLED", "true").lower() == "true"
        self._lifecycle_enabled = os.environ.get("AGENT_LIFECYCLE_ENABLED", "true").lower() == "true"

    def is_entry_enabled(self) -> bool:
        return self._entry_enabled

    def is_lifecycle_enabled(self) -> bool:
        return self._lifecycle_enabled

    async def entry_decision(
        self,
        signal: dict[str, Any],
        quant_snapshot: dict[str, Any] | None,
        experience: dict[str, Any] | None,
        open_positions: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        url = f"{self.base_url}/agent/v1/entry/decision"
        payload = {
            "signal": signal,
            "quant_snapshot": quant_snapshot,
            "experience": experience,
            "open_positions": open_positions,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("Agent harness entry decision failed: %s", exc)
            return None

    async def lifecycle_plan(
        self,
        position: dict[str, Any],
        signal_update: dict[str, Any] | None,
        quant_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        url = f"{self.base_url}/agent/v1/lifecycle/plan"
        payload = {
            "position": position,
            "signal_update": signal_update,
            "quant_snapshot": quant_snapshot,
            "recent_messages": [],
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("Agent harness lifecycle plan failed: %s", exc)
            return None
