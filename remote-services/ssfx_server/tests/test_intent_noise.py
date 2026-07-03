"""Tests that the signal-intent classifier can drop noise messages."""
from __future__ import annotations

from typing import Any

import pytest

from ssfx_parser import RawMessage, TradeSignal
from ssfx_server import web_app


class _RaisingParser:
    """Parser that fails if called — used to prove noise messages are dropped early."""

    async def parse(self, **kwargs: Any) -> TradeSignal | None:
        raise AssertionError("Parser should not be called for noise messages")

    async def close(self) -> None:
        pass


class _MemorySignalStore:
    def __init__(self) -> None:
        self.signals: list[TradeSignal] = []
        self.raw_messages: list[RawMessage] = []

    def save_raw_message(self, msg: RawMessage) -> bool:
        self.raw_messages.append(msg)
        return True

    def get_today_messages(self, chat_id: str | None = None) -> list[RawMessage]:
        return []

    def save_signal(self, signal: TradeSignal) -> bool:
        self.signals.append(signal)
        return True

    def get_signal(self, chat_id: str, message_id: int) -> TradeSignal | None:
        return None

    def get_active_signals(self, chat_id: str | None = None) -> list[TradeSignal]:
        return []


class _FakeAppState:
    def __init__(self) -> None:
        self.config = type(
            "C",
            (),
            {
                "source_chat_id": "c1",
                "agent_intent_enabled": True,
            },
        )()
        self.signal_store = _MemorySignalStore()
        self.parser = _RaisingParser()
        self.experience_scorer = None
        self.slaves = {}


@pytest.mark.asyncio
async def test_noise_intent_drops_message(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _FakeAppState()

    async def _return_noise(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"result": {"intent": "noise", "confidence": 0.95, "reasoning": "promotional"}}

    monkeypatch.setattr(web_app, "_call_signal_intent_agent", _return_noise)

    await web_app._process_channel_post(
        state,
        chat_id="c1",
        message_id=42,
        text="Check out our new crypto course!",
        reply_to_message_id=None,
    )

    assert len(state.signal_store.signals) == 0
    assert len(state.signal_store.raw_messages) == 1
