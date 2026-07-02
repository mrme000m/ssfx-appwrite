"""Base agent class with prompt rendering and JSON parsing."""

from __future__ import annotations

import logging
import time
from typing import Any

from ..config import AgentHarnessSettings, get_settings
from ..providers import LlmProvider

logger = logging.getLogger(__name__)


class BaseAgent:
    """Minimal agent harness: render prompt -> LLM -> JSON."""

    def __init__(
        self,
        model: str,
        system_prompt: str,
        settings: AgentHarnessSettings | None = None,
        provider: LlmProvider | None = None,
        timeout_seconds: float = 5.0,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> None:
        self.model = model
        self.system_prompt = system_prompt
        self._settings = settings or get_settings()
        self._provider = provider or LlmProvider(self._settings)
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def run(self, user_prompt: str) -> dict[str, Any]:
        """Return a dict with 'output' (parsed JSON) and 'metadata'."""
        start = time.perf_counter()
        try:
            resp = await self._provider.chat_completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                json_mode=True,
                timeout_seconds=self._timeout,
            )
            latency_ms = (time.perf_counter() - start) * 1000
            parsed = self._provider.parse_json_content(resp["content"])
            usage = resp.get("usage", {})
            return {
                "output": parsed,
                "metadata": {
                    "model": resp.get("model", self.model),
                    "latency_ms": round(latency_ms, 2),
                    "tokens_used": usage.get("total_tokens") if isinstance(usage, dict) else None,
                    "fallback": False,
                    "error": None,
                },
            }
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            logger.warning("Agent %s failed: %s", self.__class__.__name__, exc)
            return {
                "output": self._fallback_output(),
                "metadata": {
                    "model": self.model,
                    "latency_ms": round(latency_ms, 2),
                    "tokens_used": None,
                    "fallback": True,
                    "error": str(exc),
                },
            }

    def _fallback_output(self) -> dict[str, Any]:
        """Override in subclasses for deterministic fallback."""
        return {}
