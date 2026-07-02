"""Async LLM provider wrapper with OpenAI-compatible chat completions."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, cast

import httpx

from .config import AgentHarnessSettings, get_settings

logger = logging.getLogger(__name__)


class LlmProvider:
    """Provider-agnostic async chat completion client."""

    def __init__(self, settings: AgentHarnessSettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0, read=60.0, write=5.0))

    async def close(self) -> None:
        await self._client.aclose()

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = True,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        """Call the chat completions endpoint and return parsed content + usage."""
        base_url = self._settings.base_url_for(model)
        api_key = self._settings.api_key_for(model)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{base_url}/chat/completions"
        try:
            resp = await self._client.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
            )
            resp.raise_for_status()
            data = cast(dict[str, Any], resp.json())
        except httpx.TimeoutException as exc:
            raise asyncio.TimeoutError(f"LLM request timed out after {timeout_seconds}s") from exc
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500]
            raise RuntimeError(f"LLM API {exc.response.status_code}: {body}") from exc

        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("LLM returned no choices")

        content = choices[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        return {
            "content": str(content),
            "model": str(data.get("model", model)),
            "usage": usage,
        }

    @staticmethod
    def parse_json_content(content: str) -> dict[str, Any]:
        """Best-effort JSON extraction from LLM output."""
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
        try:
            return cast(dict[str, Any], json.loads(text))
        except json.JSONDecodeError:
            pass
        # Try to find the first JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return cast(dict[str, Any], json.loads(text[start : end + 1]))
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Could not parse LLM response as JSON: {content[:200]}")
