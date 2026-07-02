"""PPLX Research Agent — long-term market picture from Perplexity Spaces."""

from __future__ import annotations

import logging
import time
from typing import Any

from ..config import AgentHarnessSettings, get_settings
from ..tools.pplx_agent import PplxAgentClient

logger = logging.getLogger(__name__)


class PplxResearchAgent:
    """Agent wrapper that enriches decision context with PPLX Space research.

    Unlike the LLM agents, this agent does not call a language model directly.
    It calls the dedicated PPLX Agent service (Perplexity + TradingView + quant
    synthesis) and returns structured context for downstream entry/lifecycle
    agents.
    """

    def __init__(self, settings: AgentHarnessSettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: PplxAgentClient | None = None

    async def _client_ctx(self) -> PplxAgentClient:
        if self._client is None:
            self._client = PplxAgentClient(self._settings)
        return self._client

    async def run(self, question: str = "") -> dict[str, Any]:
        """Return long-term picture plus optional custom question result."""
        if not self._settings.pplx_agent_enabled:
            return {
                "output": {"enabled": False, "context": ""},
                "metadata": {"fallback": True, "error": "PPLX Agent disabled"},
            }

        start = time.perf_counter()
        client = await self._client_ctx()
        try:
            picture = await client.get_long_term_picture()
            custom = None
            if question:
                url = f"{self._settings.pplx_agent_url.rstrip('/')}/api/v1/gold/query"
                resp = await client._client.post(
                    url,
                    json={"query": question, "mode": "pro"},
                )
                resp.raise_for_status()
                custom = resp.json()

            latency_ms = (time.perf_counter() - start) * 1000
            output: dict[str, Any] = {
                "enabled": True,
                "context": picture.get("answer", "") if isinstance(picture, dict) else "",
            }
            if custom:
                output["custom_answer"] = custom.get("answer", "")
            return {
                "output": output,
                "metadata": {
                    "latency_ms": round(latency_ms, 2),
                    "fallback": picture is None,
                    "error": None if picture else "PPLX Agent returned empty response",
                },
            }
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            logger.warning("PplxResearchAgent failed: %s", exc)
            return {
                "output": {"enabled": self._settings.pplx_agent_enabled, "context": ""},
                "metadata": {
                    "latency_ms": round(latency_ms, 2),
                    "fallback": True,
                    "error": str(exc),
                },
            }

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
