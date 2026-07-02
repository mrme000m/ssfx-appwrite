"""Tests for the signal intent agent."""

from __future__ import annotations

import json

import pytest

from agent_harness.agents.signal_intent import SignalIntentAgent
from agent_harness.config import AgentHarnessSettings
from agent_harness.providers import LlmProvider


class _FakeProvider(LlmProvider):
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response

    async def close(self) -> None:
        pass

    async def chat_completion(self, *args, **kwargs):
        return {
            "content": json.dumps(self._response),
            "model": "fake",
            "usage": {},
        }


@pytest.fixture
def settings() -> AgentHarnessSettings:
    return AgentHarnessSettings(
        llm_api_key="",
        agent_model_mistral="mistral-fake",
        agent_harness_port=9003,
    )


@pytest.mark.asyncio
async def test_intent_classifier_new_signal(settings: AgentHarnessSettings) -> None:
    provider = _FakeProvider({
        "intent": "new_signal",
        "linked_message_id": None,
        "confidence": 0.92,
        "reasoning": "Clear BUY signal",
    })
    agent = SignalIntentAgent(model="mistral-fake", settings=settings, provider=provider)
    result = await agent.classify({
        "raw_text": "XAUUSD BUY 2345 SL 2340 TP 2350",
        "message_id": 100,
        "chat_id": "-1",
        "reply_to_message_id": None,
        "recent_messages": [],
    })
    assert result["result"]["intent"] == "new_signal"
    assert result["result"]["confidence"] == 0.92


@pytest.mark.asyncio
async def test_intent_classifier_links_update(settings: AgentHarnessSettings) -> None:
    provider = _FakeProvider({
        "intent": "update_to_existing",
        "linked_message_id": 99,
        "confidence": 0.88,
        "reasoning": "TP hit for prior signal",
    })
    agent = SignalIntentAgent(model="mistral-fake", settings=settings, provider=provider)
    result = await agent.classify({
        "raw_text": "TP1 HIT +50 pips",
        "message_id": 101,
        "chat_id": "-1",
        "reply_to_message_id": None,
        "recent_messages": [],
    })
    assert result["result"]["intent"] == "update_to_existing"
    assert result["result"]["linked_message_id"] == 99


@pytest.mark.asyncio
async def test_intent_fallback_on_error(settings: AgentHarnessSettings) -> None:
    class _BrokenProvider(LlmProvider):
        async def close(self) -> None:
            pass

        async def chat_completion(self, *args, **kwargs):
            raise RuntimeError("boom")

    agent = SignalIntentAgent(model="mistral-fake", settings=settings, provider=_BrokenProvider())
    result = await agent.classify({
        "raw_text": "anything",
        "message_id": 1,
        "chat_id": "-1",
    })
    assert result["result"]["intent"] == "unknown"
    assert result["metadata"]["fallback"] is True
